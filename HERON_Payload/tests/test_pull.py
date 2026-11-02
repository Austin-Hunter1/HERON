import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:                       # the tool runs with `uv run`, which installs PyYAML
    yaml = None

spec = importlib.util.spec_from_file_location("heron_pull", Path(__file__).resolve().parents[1] / "tools/heron_pull.py")
pull = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pull)

SESSION = dict(
    name="HERON_20261102_170501_123456", setup="Bench", complete=True, simulated=False, overflows=0, gaps=0,
    radios=[dict(id="b210", model="b210", serial="31C03F6", format="sc8", rate_hz=4.0, freq_hz=1176.45e6,
                 files=[dict(name="settings.json"), dict(name="samples.jsonl")],
                 channels=[dict(n=0, label="L5 direct", gain_db=60.0, port="RX2", bytes=24, seconds=3.0,
                                segments=[dict(name="channel_0_000000.sc8"), dict(name="channel_0_000001.sc8")]),
                           dict(n=1, label="Reflected LHCP", gain_db=62.0, port="RX2", bytes=24, seconds=3.0,
                                segments=[dict(name="channel_1_000000.sc8"), dict(name="channel_1_000001.sc8")])])])


class FakeNuc:
    """The NUC's files for SESSION. Segment 0 holds 2 samples, segment 1 holds 1 (4 samples/s)."""

    def __init__(self):
        rows = [dict(sample_offset=0, count=2, device_time=0.0, segment=0, segment_offset=0, uhd_error=0),
                dict(sample_offset=2, count=1, device_time=1.0, segment=1, segment_offset=0, uhd_error=0)]
        self.files = {
            ("", "session.json"): b"{}", ("", "completion.json"): b'{"complete": true}',
            ("b210", "settings.json"): json.dumps(dict(bandwidth_mhz="15", clock="external", sc8_peak="1",
                                                       write_mode="direct", spb="10000")).encode(),
            ("b210", "samples.jsonl"): "\n".join(json.dumps(r) for r in rows).encode(),
            ("b210", "channel_0_000000.sc8"): bytes(range(16)), ("b210", "channel_0_000001.sc8"): bytes(range(16, 24)),
            ("b210", "channel_1_000000.sc8"): bytes(16), ("b210", "channel_1_000001.sc8"): bytes(8),
        }

    def stream(self, path, session, name, radio=""):
        if (radio, name) not in self.files:
            raise RuntimeError("File not found.")
        data = self.files[(radio, name)]
        yield data[:5]
        yield data[5:]


@unittest.skipIf(yaml is None, "PyYAML is needed (uv run installs it)")
class PullTests(unittest.TestCase):
    def test_names_start_with_utc_time_and_band(self):
        p = pull.plan_session(SESSION, "X")
        self.assertEqual([c["collect"] for c in p["collects"]],
                         ["20261102_170501_l5_direct", "20261102_170501_l5_reflected_lhcp"])
        self.assertEqual(p["collects"][0]["collection_id"], "20261102_170501_l5_direct_3s")
        self.assertEqual(p["collects"][0]["channel_config"], "b210_l5_4e-06msps")

    def test_download_joins_segments_and_writes_yaml(self):
        with tempfile.TemporaryDirectory() as d:
            names = pull.download_session(FakeNuc(), SESSION, d, "20261102_Bench", report=lambda m: None)
            exp = Path(d) / "20261102_Bench"
            self.assertEqual((exp / (names[0] + ".dat")).read_bytes(), bytes(range(24)))      # segments in order
            side = yaml.safe_load((exp / (names[0] + ".yml")).read_text())
            self.assertEqual((side["gain_db"], side["radio_profile"]["bw_hz"], side["device_args"]), (60.0, 15e6, "serial=31C03F6"))
            self.assertEqual(side["drops"]["total"], 1)               # device time jumps 0.5 s at segment 1
            meta = yaml.safe_load((exp / "metadata.yml").read_text())
            self.assertEqual(set(meta["collections"]), {"20261102_170501_l5_direct_3s", "20261102_170501_l5_reflected_lhcp_3s"})
            self.assertEqual(meta["band_configurations"]["L5"]["center_freq"], 1176.45e6)
            self.assertTrue((exp / "nuc" / SESSION["name"] / "b210" / "samples.jsonl").exists())
            # A second pull of the same session does nothing.
            self.assertEqual(pull.download_session(FakeNuc(), SESSION, d, "20261102_Bench", report=lambda m: None), [])

    def test_existing_metadata_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            exp = Path(d) / "E"
            exp.mkdir()
            (exp / "metadata.yml").write_text("description: old\ncollections:\n  20261001_120000_l1_x_120s:\n    filename: a.dat\n")
            pull.download_session(FakeNuc(), SESSION, d, "E", report=lambda m: None)
            meta = yaml.safe_load((exp / "metadata.yml").read_text())
            self.assertEqual(meta["description"], "old")
            self.assertEqual(len(meta["collections"]), 3)

    def test_pick_newest_finished(self):
        sessions = [dict(name="c", complete=None), dict(name="b", complete=True), dict(name="a", complete=True)]
        self.assertEqual([s["name"] for s in pull.pick(sessions, 1, None, False)], ["b"])
        self.assertEqual([s["name"] for s in pull.pick(sessions, 2, None, True)], ["c", "b"])


page_spec = importlib.util.spec_from_file_location("heron_pull_page", Path(__file__).resolve().parents[1] / "tools/heron_pull_page.py")


@unittest.skipIf(yaml is None, "PyYAML is needed (uv run installs it)")
class PageTests(unittest.TestCase):
    def setUp(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        self.page = importlib.util.module_from_spec(page_spec)
        page_spec.loader.exec_module(self.page)
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self.tmp.name) / "collects"

    def tearDown(self):
        self.tmp.cleanup()

    def puller(self, nuc):
        p = self.page.Puller(Path(self.tmp.name) / "settings.json", nuc_factory=lambda base, timeout=30: nuc)
        p.settings["dest"] = str(self.dest)
        return p

    def wait(self, p, job_id, timeout=10):
        import time
        end = time.time() + timeout
        while time.time() < end:
            job = next(j for j in p.job_list() if j["id"] == job_id)
            if job["state"] not in ("queued", "running"):
                return job
            time.sleep(0.02)
        self.fail("job did not finish")

    def test_download_from_the_page_queue(self):
        nuc = FakeNuc()
        nuc.get_json = lambda path: dict(sessions=[SESSION])
        p = self.puller(nuc)
        self.assertEqual(p.sessions()[0]["pulled_into"], None)
        job = self.wait(p, p.add(SESSION["name"], "20261102_Bench")["id"])
        self.assertEqual((job["state"], job["done"], job["total"]), ("done", 48, 48))
        self.assertTrue((self.dest / "20261102_Bench" / "20261102_170501_l5_direct.dat").exists())
        self.assertEqual(p.sessions()[0]["pulled_into"], "20261102_Bench")
        self.assertEqual(p.experiments(), ["20261102_Bench"])

    def test_cancel_removes_the_partial_file(self):
        nuc = FakeNuc()
        nuc.get_json = lambda path: dict(sessions=[SESSION])
        p = self.puller(nuc)
        holder = {}
        real = nuc.stream

        def slow_stream(path, **q):
            for block in real(path, **q):
                if q["name"].startswith("channel_"):
                    p.cancel(holder["id"])             # the operator presses Cancel during the first sample file
                yield block
        nuc.stream = slow_stream
        holder["id"] = p.add(SESSION["name"], "E")["id"]
        job = self.wait(p, holder["id"])
        self.assertEqual(job["state"], "cancelled")
        self.assertEqual(list((self.dest / "E").glob("*.dat*")), [])
        self.assertIsNone(self.page.pull.pulled_into(self.dest, SESSION["name"]))

    def test_bad_input_is_refused(self):
        p = self.puller(FakeNuc())
        with self.assertRaisesRegex(ValueError, "session"):
            p.add("../../etc", "E")
        with self.assertRaisesRegex(ValueError, "Experiment"):
            p.add(SESSION["name"], "..\Windows")
        with self.assertRaisesRegex(ValueError, "NUC address"):
            p.save_settings("ftp://x", str(self.dest))
