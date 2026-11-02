import copy
import json
import tempfile
import time
import unittest
from pathlib import Path

from heron_payload import sweeps
from heron_payload.backends import FakeBackend
from heron_payload.config import presets
from heron_payload.controller import Controller


def gauss_file(path, sigma, n=200_000, seed=1):
    import random
    rnd = random.Random(seed)
    path.write_bytes(bytes(max(-128, min(127, int(round(rnd.gauss(0, sigma))))) & 0xFF for _ in range(n)))
    return path


def ready_controller(root, setup=None):
    backend = FakeBackend(write_files=True, storage_root=root)
    c = Controller(backend)
    c.execute("prepare", dict(config=setup or copy.deepcopy(presets()[0])))
    return c


def run(runner, kind, params, timeout=30):
    runner.start(kind, params)
    runner.thread.join(timeout)
    return runner.snapshot()


class ScoringTests(unittest.TestCase):
    def test_sigma_and_clipping_follow_the_wizard(self):
        with tempfile.TemporaryDirectory() as d:
            ok = sweeps.iq_quality(gauss_file(Path(d) / "a.sc8", 28), "sc8", 4.5, 0.98)
            self.assertAlmostEqual(ok["sigma_fraction"], 28 / 127, delta=0.01)
            self.assertLess(ok["clip_fraction"], 1e-4)
            hot = sweeps.iq_quality(gauss_file(Path(d) / "b.sc8", 120), "sc8", 4.5, 0.98)
            self.assertGreater(hot["clip_fraction"], 0.2)
            quiet = sweeps.iq_quality(gauss_file(Path(d) / "c.sc8", 2), "sc8", 4.5, 0.98)
            self.assertGreater(ok["score"], quiet["score"])
            self.assertGreater(ok["score"], hot["score"])

    def test_percentile_matches_linear_interpolation(self):
        from collections import Counter
        values = [1, 2, 3, 4]
        self.assertEqual(sweeps._percentile(Counter(values), 4, 0.5), 2.5)
        self.assertEqual(sweeps._percentile(Counter(values), 4, 0.0), 1.0)

    def test_prediction_uses_median_of_clean_trials(self):
        # sigma_dB - gain = -60 for the clean trials; one interference-hit trial (-40) is outvoted.
        trials = [dict(gain=g, clip_fraction=0, sigma_fraction=10 ** ((g - 60) / 20)) for g in (20, 30, 40)]
        trials.append(dict(gain=50, clip_fraction=0, sigma_fraction=10 ** ((50 - 40) / 20)))
        trials.append(dict(gain=70, clip_fraction=0.3, sigma_fraction=0.9))
        target_db = 20 * __import__("math").log10(1 / 4.5)
        self.assertAlmostEqual(sweeps.predict_gain(trials, 4.5), target_db + 60, places=3)
        self.assertIsNone(sweeps.predict_gain(trials[:1], 4.5))

    def test_fine_gains_exclude_the_ends(self):
        self.assertEqual(sweeps.fine_gains_between(60, 70, 2), [62, 64, 66, 68])
        self.assertEqual(sweeps.fine_gains_between(60, 61, 2), [])

    def test_params_are_checked(self):
        with self.assertRaisesRegex(ValueError, "two coarse gains"):
            sweeps.check_gain_params(dict(gains=[40]))
        with self.assertRaisesRegex(ValueError, "USB buffer"):
            sweeps.check_buffer_params(dict(device_args=["num_recv_frames=256;reboot"]))
        with self.assertRaisesRegex(ValueError, "trials"):
            sweeps.check_buffer_params(dict(spb=list(range(100, 10100, 100))))
        p = sweeps.check_buffer_params({})
        self.assertEqual(len(sweeps.buffer_trials(p)), 2 * 3 * 3 * 2)


class RunnerTests(unittest.TestCase):
    def test_gain_sweep_finds_a_gain_per_channel_and_restores_the_setup(self):
        with tempfile.TemporaryDirectory() as d:
            c = ready_controller(Path(d) / "rec")
            original = copy.deepcopy(c.config)
            runner = sweeps.SweepRunner(c, Path(d) / "sweeps", sleep=lambda s: None)
            s = run(runner, "gain", dict(gains=[30, 50, 70], seconds=1))
            self.assertIsNone(s["error"])
            self.assertFalse(s["running"])
            best = s["result"]["best"]
            self.assertEqual(len(best), 2)
            # The simulated radio puts 4.5 sigma at full scale near 64 dB (backends.FakeBackend._write_noise).
            for b in best:
                self.assertTrue(58 <= b["gain"] <= 70, b)
            phases = {t["phase"] for t in s["result"]["trials"]}
            self.assertEqual(phases, {"coarse", "predicted", "fine"})
            self.assertEqual(c.state, "READY")
            self.assertEqual(c.config, original)
            self.assertEqual(list((Path(d) / "rec").iterdir()), [])          # trial recordings removed
            saved = json.loads(Path(s["saved"]).read_text())
            self.assertEqual(saved["kind"], "gain")

    def test_buffer_sweep_runs_every_combination(self):
        with tempfile.TemporaryDirectory() as d:
            c = ready_controller(Path(d) / "rec")
            runner = sweeps.SweepRunner(c, Path(d) / "sweeps", sleep=lambda s: None)
            s = run(runner, "buffer", dict(write_modes=["direct", "threaded"], spb=[10000], device_args=["", "num_recv_frames=256"],
                                           file_buffer_bytes=[0], seconds=1, keep=True))
            rows = s["result"]["trials"]
            self.assertEqual([(r["write_mode"], r["device_args"]) for r in rows],
                             [("direct", ""), ("direct", "num_recv_frames=256"), ("threaded", ""), ("threaded", "num_recv_frames=256")])
            self.assertTrue(all(r["clean"] for r in rows))
            self.assertEqual(s["result"]["recommended"], 1)
            self.assertEqual(len(list((Path(d) / "rec").iterdir())), 4)      # keep=True
            self.assertEqual(c.config["write_mode"], "direct")

    def test_sweep_needs_a_ready_payload(self):
        with tempfile.TemporaryDirectory() as d:
            c = Controller(FakeBackend())
            runner = sweeps.SweepRunner(c, Path(d))
            with self.assertRaisesRegex(ValueError, "Load a setup"):
                runner.start("gain", {})

    def test_stop_ends_the_sweep_early(self):
        with tempfile.TemporaryDirectory() as d:
            c = ready_controller(Path(d) / "rec")
            runner = sweeps.SweepRunner(c, Path(d) / "sweeps", sleep=lambda s: time.sleep(0.01))
            runner.start("buffer", dict(spb=[1000, 2000, 3000], seconds=2))
            time.sleep(0.3)
            runner.stop()
            runner.thread.join(20)
            s = runner.snapshot()
            self.assertFalse(s["running"])
            self.assertIn("Stopped", s["error"])
            self.assertLess(len(s["result"]["trials"]), 36)
            self.assertEqual(c.state, "READY")
