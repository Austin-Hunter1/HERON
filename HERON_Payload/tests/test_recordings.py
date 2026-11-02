import base64
import copy
import tempfile
import unittest
from pathlib import Path

from heron_payload import recordings, storage
from heron_payload.backends import FakeBackend
from heron_payload.config import presets
from heron_payload.controller import Controller
from heron_payload.setup_app import create_app


def single_b200(path):
    c = copy.deepcopy(presets()[0])
    c["name"] = "One B200"
    c["radios"][0].update(model="b200", channels=c["radios"][0]["channels"][:1])
    c["storage"]["path"] = path
    return c


class StorageTests(unittest.TestCase):
    def test_missing_data_drive_falls_back_to_ssd(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = single_b200("/media/NoSuchDriveForTests/heron")
            c["storage"]["require_mount"] = "/media/NoSuchDriveForTests"
            w = storage.resolve(c, tmp)
            self.assertTrue(w["fallback"])
            self.assertEqual(w["path"], tmp)
            self.assertEqual(w["drive"], storage.SYSTEM_SSD)
            self.assertLessEqual(len("HERON " + w["note"]), 50)     # fits one MAVLink STATUSTEXT

    def test_plain_folder_is_used_as_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = single_b200(tmp + "/rec")
            c["storage"]["require_mount"] = ""
            w = storage.resolve(c, "/nowhere")
            self.assertFalse(w["fallback"])
            self.assertEqual(w["path"], tmp + "/rec")

    def test_fallback_is_announced_and_recorded_to_ssd(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = single_b200("/media/NoSuchDriveForTests/heron")
            ctl = Controller(FakeBackend(write_files=True, ssd_dir=tmp))
            ctl.execute("prepare", dict(config=c))
            self.assertTrue(any("NUC SSD" in e["text"] for e in ctl.events))
            ctl.execute("start")
            ctl.execute("stop")
            self.assertEqual(len([p for p in Path(tmp).iterdir() if p.name.startswith("HERON_")]), 1)
            self.assertTrue(ctl.snapshot()["storage"]["fallback"])


class RecordingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ctl = Controller(FakeBackend(write_files=True, ssd_dir=self.root))
        self.ctl.execute("prepare", dict(config=single_b200("/media/NoSuchDriveForTests/heron")))
        self.ctl.execute("start")
        self.ctl.execute("stop")
        self.session = next(p for p in self.root.iterdir() if p.name.startswith("HERON_"))
        self.app = create_app(self.ctl, self.root / "state", ssd_dir=self.root).test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_describes_channels(self):
        rows = recordings.list_sessions([self.root])
        self.assertEqual(rows[0]["name"], self.session.name)
        self.assertEqual(rows[0]["setup"], "One B200")
        ch = rows[0]["radios"][0]["channels"]
        self.assertEqual(len(ch), 1)
        self.assertGreater(ch[0]["seconds"], 0)
        self.assertTrue(rows[0]["complete"])

    def test_sample_slices(self):
        d = recordings.sample_channel(self.session, "b210", 0, rows=10, nfft=256, avg=2)
        raw = base64.b64decode(d["data"])
        self.assertEqual(len(raw), d["rows"] * d["row_samples"] * 2)
        self.assertEqual(d["row_samples"], 512)
        self.assertEqual(len(d["times"]), d["rows"])
        self.assertEqual(d["times"], sorted(d["times"]))

    def test_unsafe_paths_rejected(self):
        for radio, name in ((None, "../etc/passwd"), ("..", "settings.json"), ("b210", "run.sh"), ("b210", ".hidden.json")):
            with self.assertRaises(ValueError):
                recordings.file_path(self.session, radio, name)
        with self.assertRaises(ValueError):
            recordings.find_session([self.root], "../../etc")
        self.assertTrue(recordings.file_path(self.session, "b210", "settings.json").is_file())

    def test_http_endpoints(self):
        r = self.app.get("/api/recordings").get_json()
        self.assertEqual(r["sessions"][0]["name"], self.session.name)
        v = self.app.get(f"/api/recordings/view?session={self.session.name}&radio=b210&channel=0&rows=20")
        self.assertEqual(v.status_code, 200, v.get_json())
        self.assertEqual(v.get_json()["format"], "sc8")
        f = self.app.get(f"/api/recordings/file?session={self.session.name}&radio=b210&name=channel_0_000000.sc8")
        self.assertEqual(f.status_code, 200)
        self.assertIn("attachment", f.headers["Content-Disposition"])
        self.assertEqual(len(f.data), (self.session / "b210" / "channel_0_000000.sc8").stat().st_size)
        f.close()
        s = self.app.get("/api/state").get_json()
        self.assertEqual(s["disk"]["drive"], storage.SYSTEM_SSD)
        self.assertTrue(s["disk"]["fallback"])

    def test_viewing_paused_while_recording(self):
        self.ctl.execute("start")
        v = self.app.get(f"/api/recordings/view?session={self.session.name}&radio=b210&channel=0")
        self.assertEqual(v.status_code, 409)
        f = self.app.get(f"/api/recordings/file?session={self.session.name}&radio=b210&name=settings.json")
        self.assertEqual(f.status_code, 409)
        self.assertEqual(self.app.get("/api/recordings").status_code, 200)
        self.ctl.execute("stop")



class RealRecorderFormatTests(unittest.TestCase):
    def test_signal_check_reads_string_numbers_from_cpp_recorder(self):
        """The UHD recorder's settings.json has every value as a string (Boost property_tree)."""
        import json
        from heron_payload.analysis import analyse_session
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "HERON_20261102_162449_255708" / "b210_2_92w"
            d.mkdir(parents=True)
            (d / "settings.json").write_text(json.dumps({"sample_rate_msps": "22", "frequency_mhz": "1176.45",
                "actual_channels": [{"physical_channel": "0", "label": "RF A", "gain_db": "45",
                                     "sample_rate_hz": "22000000", "port": "RX2"}]}))
            (d / "channel_0_000000.sc8").write_bytes(bytes([10, 246, 30, 226] * 5000))
            row = analyse_session(d.parent)[0]
            self.assertEqual(row["gain_db"], 45.0)
            self.assertIsInstance(row["suggested_gain_db"], float)
            info = recordings.describe_radio(d)
            self.assertEqual(info["rate_hz"], 22e6)
            self.assertEqual(info["channels"][0]["gain_db"], 45.0)


class LongTestRecordingTests(unittest.TestCase):
    """Bench recordings from the page: timed up to an hour, or until Stop."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ctl = Controller(FakeBackend(write_files=True, ssd_dir=self.root))
        self.ctl.execute("prepare", dict(config=single_b200("/media/NoSuchDriveForTests/heron")))
        self.app = create_app(self.ctl, self.root / "state", ssd_dir=self.root).test_client()
        self.h = {"X-Heron": "1"}

    def tearDown(self):
        self.tmp.cleanup()

    def wait_done(self, limit=5):
        import time
        end = time.monotonic() + limit
        while time.monotonic() < end:
            t = self.app.get("/api/state").get_json()["test"]
            if not t["running"]:
                return t
            time.sleep(0.05)
        self.fail("test recording did not finish")

    def test_lengths_accepted_and_rejected(self):
        for bad in (-1, 0.5, 3601, "10"):
            r = self.app.post("/api/test", json={"seconds": bad}, headers=self.h)
            self.assertEqual(r.status_code, 409, bad)
        r = self.app.post("/api/test", json={"seconds": 1800}, headers=self.h)
        self.assertEqual(r.status_code, 200)
        st = self.app.get("/api/state").get_json()["test"]
        self.assertTrue(st["running"])
        self.assertEqual(st["seconds"], 1800)
        self.assertGreater(st["remaining_s"], 1790)
        self.app.post("/api/test/stop", json={}, headers=self.h)
        t = self.wait_done()
        self.assertIsNotNone(t["result"], t)

    def test_until_stop(self):
        r = self.app.post("/api/test", json={"seconds": 0}, headers=self.h)
        self.assertEqual(r.status_code, 200)
        import time
        time.sleep(0.5)
        st = self.app.get("/api/state").get_json()["test"]
        self.assertTrue(st["running"])
        self.assertIsNone(st["remaining_s"])
        self.assertGreater(st["elapsed_s"], 0.3)
        self.assertEqual(self.ctl.state, "RECORDING")
        self.app.post("/api/test/stop", json={}, headers=self.h)
        t = self.wait_done()
        self.assertEqual(self.ctl.state, "READY")
        self.assertEqual(len(t["result"]), 1)

    def test_page_stop_never_stops_a_flight_recording(self):
        self.ctl.execute("start", dict(origin="manual"))
        self.app.post("/api/test/stop", json={}, headers=self.h)
        self.assertEqual(self.ctl.state, "RECORDING")
        self.ctl.execute("stop")

    def test_test_ends_if_recording_stopped_elsewhere(self):
        self.app.post("/api/test", json={"seconds": 0}, headers=self.h)
        self.ctl.execute("stop")                 # e.g. Stop from the ground station
        t = self.wait_done()
        self.assertFalse(t["running"])


if __name__ == "__main__":
    unittest.main()


def _jsonl_session(root, rows, rate_msps=1.0):
    """A finished session with one radio whose samples.jsonl holds `rows` (offset, count, time, error)."""
    import json
    radio = Path(root) / "HERON_20261102_120000" / "b210"
    radio.mkdir(parents=True)
    (radio / "settings.json").write_text(json.dumps(dict(sample_rate_msps=rate_msps, frequency_mhz=1176.45,
                                                         actual_channels=[dict(label="A"), dict(label="B")])))
    seg = int(rate_msps * 1e6 * 30)
    lines = []
    for offset, count, t, err in rows:
        lines.append('{"sample_offset":%d,"count":%d,"device_time":%.17g,"segment":%d,"segment_offset":%d,"uhd_error":%d}'
                     % (offset, count, t, offset // seg, offset % seg, err))
    (radio / "samples.jsonl").write_text("\n".join(lines) + "\n")
    for n in (0, 1):
        (radio / f"channel_{n}_000000.sc8").write_bytes(b"\0" * 8)
    return radio.parent


class DropTests(unittest.TestCase):
    def test_drops_are_located_in_file_and_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 1 MS/s, blocks of 1000: a 500-sample gap before offset 2000, a UHD error flag at 3000.
            d = _jsonl_session(tmp, [(0, 1000, 5.0, 0), (1000, 1000, 5.001, 0), (2000, 1000, 5.0025, 0),
                                     (3000, 1000, 5.0035, 8), (4000, 1000, 5.0045, 0)])
            r = recordings.find_drops(d, "b210")
            self.assertEqual((r["total"], r["missing_samples"], r["rows"]), (2, 500, 5))
            lost, flag = r["drops"]
            self.assertEqual((lost["kind"], lost["missing"], lost["sample"], lost["segment"], lost["segment_offset"]),
                             ("samples lost", 500, 2000, 0, 2000))
            self.assertAlmostEqual(lost["at_s"], 0.002)
            self.assertAlmostEqual(lost["device_s"], 0.0025)
            self.assertAlmostEqual(lost["missing_ms"], 0.5)
            self.assertEqual((flag["kind"], flag["uhd_error"], flag["missing"]), ("UHD error", 8, 0))
            self.assertEqual(r["recorded_s"], 0.005)
            self.assertEqual([c["n"] for c in r["channels"]], [0, 1])

    def test_clean_recording_and_segment_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 30 s segments at 1 MS/s: offset 31e6 is segment 1, 1e6 samples into it.
            rows = [(0, 1000, 0.0, 0), (1000, 1000, 0.001, 0)]
            d = _jsonl_session(tmp, rows)
            self.assertEqual(recordings.find_drops(d, "b210")["total"], 0)
        with tempfile.TemporaryDirectory() as tmp:
            d = _jsonl_session(tmp, [(30_999_000, 1000, 31.0, 0), (31_000_000, 1000, 31.5, 0)])
            x = recordings.find_drops(d, "b210")["drops"][0]
            self.assertEqual((x["segment"], x["segment_offset"], x["missing"]), (1, 1_000_000, 499_000))

    def test_missing_jsonl_and_bad_radio_are_explained(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = _jsonl_session(tmp, [(0, 10, 0.0, 0)])
            (d / "b210" / "samples.jsonl").unlink()
            with self.assertRaisesRegex(ValueError, "samples.jsonl"):
                recordings.find_drops(d, "b210")
            with self.assertRaisesRegex(ValueError, "Unknown radio"):
                recordings.find_drops(d, "../etc")
