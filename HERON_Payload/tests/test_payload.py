import copy
import tempfile
import unittest
from pathlib import Path

from heron_payload import protocol as P
from heron_payload.analysis import analyse_file, analyse_session
from heron_payload.backends import FakeBackend
from heron_payload.config import ProfileStore, estimate, presets, validate
from heron_payload.controller import Controller


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def ready(setup=None, **kw):
    clock = Clock()
    c = Controller(FakeBackend(**kw), clock=clock)
    c.execute("prepare", dict(config=setup or presets()[0]))
    c.cube_update(armed=False, mode="STABILIZE", current=0)
    return c, clock


def fly_to(c, seq):
    """What ArduPilot does on arrival: MISSION_ITEM_REACHED(seq), then MISSION_CURRENT(seq+1)."""
    c.cube_update(reached=seq)
    c.cube_update(current=seq + 1)


class ConfigTests(unittest.TestCase):
    def test_default_is_one_b210_two_channels_l5(self):
        c = validate(presets()[0])
        self.assertEqual([r["model"] for r in c["radios"]], ["b210"])
        e = estimate(c)
        self.assertEqual((e["channels"], e["disk_mbps"]), (2, 88.0))

    def test_surge_preset_records_channel_b_only(self):
        c = validate(presets()[1])
        self.assertEqual([ch["enabled"] for ch in c["radios"][0]["channels"]], [False, True])
        self.assertEqual(estimate(c)["disk_mbps"], 44.0)

    def test_bad_values_are_explained(self):
        c = copy.deepcopy(presets()[0])
        c["radios"][0]["channels"][0]["gain_db"] = 99
        with self.assertRaisesRegex(ValueError, "Gain"):
            validate(c)
        c = copy.deepcopy(presets()[0])
        c["radios"][0]["bandwidth_mhz"] = 40
        with self.assertRaisesRegex(ValueError, "bandwidth"):
            validate(c)
        c = copy.deepcopy(presets()[0])
        c["sc8_peak"] = 3
        with self.assertRaisesRegex(ValueError, "8-bit"):
            validate(c)

    def test_collect_wizard_recording_method_is_the_default(self):
        # D-078: sc8 on the USB link with peak 1.0, direct writes, user bandwidth (wizard 15 MHz option).
        c = validate(presets()[0])
        self.assertEqual((c["sc8_peak"], c["write_mode"], c["spb"], c["file_buffer_bytes"]), (1.0, "direct", 10000, 0))
        self.assertEqual(c["radios"][0]["bandwidth_mhz"], 15.0)
        self.assertEqual(estimate(c)["usb_mbps"], estimate(c)["disk_mbps"])

    def test_old_setups_get_buffer_defaults(self):
        c = copy.deepcopy(presets()[0])
        for key in ("write_mode", "spb", "file_buffer_bytes", "device_args"):
            del c[key]
        c = validate(c)
        self.assertEqual((c["write_mode"], c["spb"], c["device_args"]), ("direct", 10000, ""))

    def test_buffer_settings_are_checked(self):
        for key, value, message in (("write_mode", "fast", "write method"), ("spb", 10, "Samples per buffer"),
                                    ("device_args", "num_recv_frames=256;rm", "USB buffer")):
            c = copy.deepcopy(presets()[0])
            c[key] = value
            with self.assertRaisesRegex(ValueError, message):
                validate(c)
        c = copy.deepcopy(presets()[0])
        c.update(write_mode="threaded", spb=50000, file_buffer_bytes=4194304,
                 device_args="num_recv_frames=1024,recv_frame_size=16000")
        self.assertEqual(validate(c)["device_args"], "num_recv_frames=1024,recv_frame_size=16000")

    def test_hardware_needs_serials(self):
        with self.assertRaisesRegex(ValueError, "serial"):
            validate(presets()[0], hardware=True)

    def test_store_save_list_delete(self):
        with tempfile.TemporaryDirectory() as d:
            store = ProfileStore(Path(d) / "s.json")
            c = copy.deepcopy(presets()[0])
            c["name"] = "Reservoir day 2"
            store.save(dict(c, builtin=True))                       # UI sends the flag back; it is dropped
            again = ProfileStore(Path(d) / "s.json")
            row = next(p for p in again.list() if p["name"] == "Reservoir day 2")
            self.assertFalse(row["builtin"])
            with self.assertRaisesRegex(ValueError, "Built-in"):
                again.delete(presets()[0]["name"])
            again.delete("Reservoir day 2")
            self.assertNotIn("Reservoir day 2", [p["name"] for p in ProfileStore(Path(d) / "s.json").list()])


class AutoRecordTests(unittest.TestCase):
    def arm(self, c, start=4, stop=28):
        c.execute("arm", dict(start_seq=start, stop_seq=stop))
        c.cube_update(armed=True, mode="AUTO")

    def test_starts_on_arrival_not_when_heading_there(self):
        c, _ = ready()
        self.arm(c)
        c.cube_update(current=4)                     # flying towards WP 4
        self.assertEqual(c.state, "AUTO_ARMED")
        fly_to(c, 4)
        self.assertEqual((c.state, c.origin), ("RECORDING", "mission"))
        for seq in range(5, 28):
            fly_to(c, seq)
        self.assertEqual(c.state, "RECORDING")
        fly_to(c, 28)
        self.assertEqual(c.state, "READY")
        self.assertIsNone(c.plan)

    def test_late_start_if_arrival_message_missed(self):
        c, _ = ready()
        self.arm(c)
        c.cube_update(current=6)
        self.assertEqual(c.state, "RECORDING")
        self.assertIn("Late start", c.events[-1]["text"])

    def test_no_start_unless_armed_in_auto(self):
        c, _ = ready()
        c.execute("arm", dict(start_seq=4, stop_seq=28))
        c.cube_update(mode="GUIDED", armed=True)
        fly_to(c, 4)
        self.assertEqual(c.state, "AUTO_ARMED")

    def test_rtl_stops_mission_recording_and_cancels_armed(self):
        c, _ = ready()
        self.arm(c)
        fly_to(c, 4)
        c.cube_update(mode="RTL")
        self.assertEqual(c.state, "READY")
        c, _ = ready()
        self.arm(c)
        c.cube_update(mode="RTL")
        self.assertEqual((c.state, c.plan), ("READY", None))

    def test_manual_recording_survives_mode_changes_but_stops_on_disarm(self):
        c, _ = ready()
        c.cube_update(armed=True, mode="LOITER")
        c.execute("start")
        c.cube_update(mode="RTL")
        self.assertEqual(c.state, "RECORDING")
        c.cube_update(armed=False)
        self.assertEqual(c.state, "READY")

    def test_operator_stop_wins(self):
        c, _ = ready()
        self.arm(c)
        fly_to(c, 4)
        c.execute("stop")
        fly_to(c, 5)
        self.assertEqual(c.state, "READY")

    def test_arm_rules(self):
        c, _ = ready()
        with self.assertRaisesRegex(ValueError, "before"):
            c.execute("arm", dict(start_seq=9, stop_seq=4))
        c.cube_update(armed=True, mode="AUTO", current=10)
        with self.assertRaisesRegex(ValueError, "past mission item 4"):
            c.execute("arm", dict(start_seq=4, stop_seq=28))
        idle = Controller(FakeBackend())
        with self.assertRaisesRegex(ValueError, "No setup"):
            idle.execute("start")

    def test_repeat_arm_is_harmless(self):
        c, _ = ready()
        c.execute("arm", dict(start_seq=4, stop_seq=28))
        c.execute("arm", dict(start_seq=4, stop_seq=28))
        self.assertEqual(c.state, "AUTO_ARMED")

    def test_disk_reserve_faults(self):
        c, _ = ready()
        c.execute("start")
        c.backend.free = 1
        c.tick()
        self.assertEqual(c.state, "FAULT")
        with self.assertRaisesRegex(ValueError, "fault"):
            c.execute("start")

    def test_status_packing(self):
        c, _ = ready()
        self.arm(c)
        fly_to(c, 4)
        v = P.status_values(c.snapshot())
        self.assertEqual(P.STATES[int(v[P.F_STATE])], "RECORDING")
        self.assertEqual(P.unpack_auto(v[P.F_AUTO]), dict(start_seq=4, stop_seq=28))
        self.assertTrue(all(len(name) <= 10 for name in v))


class SignalCheckTests(unittest.TestCase):
    def test_noise_level_verdicts(self):
        with tempfile.TemporaryDirectory() as d:
            # peak 1.0 (the collect wizard value, D-078) has 12 dB less digital gain than SURGE-era 0.25,
            # so the simulated radio needs about 12 dB more RF gain for the same level.
            for gain, expect in ((57, ("low", "good")), (76, ("clipping",)), (5, ("too quiet",))):
                setup = copy.deepcopy(presets()[0])
                for ch in setup["radios"][0]["channels"]:
                    ch["gain_db"] = gain
                c, _ = ready(setup, write_files=True, storage_root=Path(d) / str(gain))
                c.execute("start")
                c.execute("stop")
                rows = analyse_session(c.backend.session_path())
                self.assertEqual(len(rows), 2)
                self.assertIn(rows[0]["verdict"], expect, rows[0])
                self.assertEqual(sum(rows[0]["histogram"]), rows[0]["values"])
                if rows[0]["verdict"] == "clipping":
                    self.assertLess(rows[0]["suggested_gain_db"], gain)
                if rows[0]["verdict"] == "too quiet":
                    self.assertGreater(rows[0]["suggested_gain_db"], gain)

    def test_sc8_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "channel_0_000000.sc8"
            f.write_bytes(bytes([127, 128, 0, 0] * 1000))
            r = analyse_file(f, "sc8")
            self.assertEqual(r["verdict"], "clipping")
            self.assertAlmostEqual(r["clip_pct"], 50.0)


if __name__ == "__main__":
    unittest.main()
