"""SITL overlay must not apply Aurelia attitude or 6S voltage to the 3S hexa model."""
import importlib.util
import unittest
from pathlib import Path


def load_sitl():
    path = Path(__file__).resolve().parents[1] / "scripts" / "sitl.py"
    spec = importlib.util.spec_from_file_location("sitl", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SitlOverlayTests(unittest.TestCase):
    def test_overlay_keeps_mission_speed_not_x6_dynamics(self):
        sitl = load_sitl()
        out = sitl.overlay({
            "ATC_ACCEL_P_MAX": "110000",
            "ATC_RATE_Y_MAX": "0",
            "ANGLE_MAX": "3000",
            "LOIT_SPEED": "1000",
            "WPNAV_SPEED": "800",
            "RTL_ALT": "1500",
            "BATT_ARM_VOLT": "22.5",
            "BATT_LOW_VOLT": "20.05",
            "BATT_CAPACITY": "54000",
        })
        self.assertEqual(out["WPNAV_SPEED"], "800")
        self.assertEqual(out["RTL_ALT"], "1500")
        self.assertNotIn("ATC_ACCEL_P_MAX", out)
        self.assertNotIn("ANGLE_MAX", out)
        self.assertNotIn("LOIT_SPEED", out)
        self.assertEqual(out["SIM_BATT_VOLTAGE"], "12.6")
        self.assertAlmostEqual(float(out["BATT_ARM_VOLT"]), 11.25)


if __name__ == "__main__":
    unittest.main()
