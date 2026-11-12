"""NUC temperatures: read from hwmon, sent in the MAVLink status, logged per recording."""
import csv
import tempfile
import unittest
from pathlib import Path

from heron_payload import health
from heron_payload import protocol as P
from heron_payload.backends import FakeBackend
from heron_payload.config import presets
from heron_payload.controller import Controller
from tests.test_payload import Clock


def sensor(root, name, temps, labels=None):
    d = root / f"hwmon{len(list(root.iterdir()))}"
    d.mkdir()
    (d / "name").write_text(name + "\n")
    for i, milli in enumerate(temps, 1):
        (d / f"temp{i}_input").write_text(f"{milli}\n")
        if labels:
            (d / f"temp{i}_label").write_text(labels[i - 1] + "\n")


class ReadTempsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hwmon = Path(self.tmp.name) / "hwmon"
        self.thermal = Path(self.tmp.name) / "thermal"
        self.hwmon.mkdir()
        self.thermal.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_nuc_with_coretemp_and_nvme(self):
        sensor(self.hwmon, "acpitz", [27800])
        sensor(self.hwmon, "coretemp", [61000, 58000, 63000], ["Package id 0", "Core 0", "Core 1"])
        sensor(self.hwmon, "nvme", [44850, 52850], ["Composite", "Sensor 1"])
        self.assertEqual(health.read_temps(self.hwmon, self.thermal), {"cpu_c": 61.0, "ssd_c": 44.9})

    def test_no_package_label_uses_hottest_core(self):
        sensor(self.hwmon, "coretemp", [58000, 66000])
        self.assertEqual(health.read_temps(self.hwmon, self.thermal)["cpu_c"], 66.0)

    def test_thermal_zone_fallback_and_missing_ssd(self):
        z = self.thermal / "thermal_zone3"
        z.mkdir()
        (z / "type").write_text("x86_pkg_temp\n")
        (z / "temp").write_text("57000\n")
        self.assertEqual(health.read_temps(self.hwmon, self.thermal), {"cpu_c": 57.0, "ssd_c": None})

    def test_no_sensors_at_all(self):
        self.assertEqual(health.read_temps(Path(self.tmp.name) / "nope", Path(self.tmp.name) / "nope2"),
                         {"cpu_c": None, "ssd_c": None})


class ControllerHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.temps = {"cpu_c": 55.0, "ssd_c": 40.0}
        self.clock = Clock()
        self.c = Controller(FakeBackend(write_files=True, storage_root=self.tmp.name), clock=self.clock,
                            read_temps=lambda: dict(self.temps))
        self.c.execute("prepare", dict(config=presets()[0]))

    def tearDown(self):
        self.tmp.cleanup()

    def tick(self, seconds):
        self.clock.t += seconds
        self.c.tick()

    def test_temperatures_go_out_over_mavlink(self):
        self.tick(0)
        values = P.status_values(self.c.snapshot())
        self.assertEqual((values[P.F_CPU], values[P.F_SSD]), (55.0, 40.0))
        self.temps = {"cpu_c": None, "ssd_c": None}
        self.tick(3)
        values = P.status_values(self.c.snapshot())
        self.assertEqual((values[P.F_CPU], values[P.F_SSD]), (-1.0, -1.0))
        self.assertTrue(all(len(name) <= 10 for name in P.STATUS_FIELDS))   # NAMED_VALUE_FLOAT limit

    def test_recording_writes_nuc_health_csv(self):
        self.c.execute("start")
        self.c.cube_update(current=7)
        for _ in range(4):
            self.tick(5)
        folder = self.c.backend.session_path()
        self.c.execute("stop")
        rows = list(csv.DictReader((Path(folder) / "nuc_health.csv").open()))
        self.assertEqual(len(rows), 4)
        self.assertEqual((rows[0]["cpu_c"], rows[0]["ssd_c"], rows[0]["mission_item"]), ("55.0", "40.0", "7"))
        self.tick(5)
        self.assertEqual(len(list(csv.DictReader((Path(folder) / "nuc_health.csv").open()))), 4)  # stops with the recording

    def test_hot_warning_once_then_cooled(self):
        self.tick(0)
        self.temps["cpu_c"] = 93.0
        self.tick(3)
        self.tick(3)
        self.temps["cpu_c"] = 80.0
        self.tick(3)
        texts = [e["text"] for e in self.c.events]
        self.assertEqual(sum("NUC CPU hot: 93 C" in t for t in texts), 1)
        self.assertIn("NUC CPU cooled to 80 C", texts)

    def test_broken_sensor_never_breaks_the_payload(self):
        def boom():
            raise OSError("sensor gone")
        self.c.read_temps = boom
        self.c.execute("start")
        self.tick(5)
        self.assertEqual(self.c.state, "RECORDING")
        self.assertEqual(self.c.snapshot()["temps"], {"cpu_c": None, "ssd_c": None})


if __name__ == "__main__":
    unittest.main()
