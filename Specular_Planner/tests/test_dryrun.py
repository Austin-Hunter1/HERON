"""Dry run relay rules: what crosses between ArduCopter SITL and the real flight controller."""
import os
import unittest
from unittest import mock

os.environ["MAVLINK20"] = "1"
from pymavlink.dialects.v20 import ardupilotmega as mav  # noqa: E402

from engine import sitl_link  # noqa: E402
from engine.flight import FlightError, FlightService  # noqa: E402


def packed(msg, sysid, compid):
    m = mav.MAVLink(None, srcSystem=sysid, srcComponent=compid)
    buf = msg.pack(m)
    return m.parse_char(buf)


SITL = 2


class RelayRuleTests(unittest.TestCase):
    def test_sitl_flight_state_reaches_the_nuc(self):
        for msg in (mav.MAVLink_heartbeat_message(2, 3, 0, 0, 4, 3),
                    mav.MAVLink_mission_current_message(5),
                    mav.MAVLink_mission_item_reached_message(5),
                    mav.MAVLink_system_time_message(0, 1000)):
            self.assertTrue(sitl_link.forward_to_fc(packed(msg, SITL, 1), SITL), msg.get_type())

    def test_sitl_telemetry_and_gcs_traffic_stay_off_the_real_fc(self):
        pos = mav.MAVLink_global_position_int_message(0, 0, 0, 0, 0, 0, 0, 0, 0)
        self.assertFalse(sitl_link.forward_to_fc(packed(pos, SITL, 1), SITL))
        gcs_hb = mav.MAVLink_heartbeat_message(6, 8, 0, 0, 4, 3)
        self.assertFalse(sitl_link.forward_to_fc(packed(gcs_hb, 255, 190), SITL))
        rtcm = mav.MAVLink_gps_rtcm_data_message(0, 3, b"abc" + bytes(177))
        self.assertFalse(sitl_link.forward_to_fc(packed(rtcm, 255, 190), SITL))
        # Commands to the autopilot (arm, mode, mission) never leave SITL toward the real FC.
        arm = mav.MAVLink_command_long_message(SITL, 1, 400, 0, 1, 0, 0, 0, 0, 0, 0)
        self.assertFalse(sitl_link.forward_to_fc(packed(arm, 255, 190), SITL))
        arm_real = mav.MAVLink_command_long_message(1, 1, 400, 0, 1, 0, 0, 0, 0, 0, 0)
        self.assertFalse(sitl_link.forward_to_fc(packed(arm_real, 255, 190), SITL))

    def test_payload_commands_and_acks_pass_both_ways(self):
        cmd = mav.MAVLink_command_long_message(SITL, 191, 31010, 0, 3, 4, 9, 77, 0, 0, 0)
        self.assertTrue(sitl_link.forward_to_fc(packed(cmd, 255, 190), SITL))
        ack = mav.MAVLink_command_ack_message(511, 0, 0, 0, SITL, 191)
        self.assertTrue(sitl_link.forward_to_fc(packed(ack, SITL, 1), SITL))
        nuc_ack = mav.MAVLink_command_ack_message(31010, 0, 100, 77, 255, 190)
        self.assertTrue(sitl_link.forward_to_sitl(packed(nuc_ack, SITL, 191)))

    def test_only_the_nuc_goes_back_to_sitl(self):
        fc_hb = mav.MAVLink_heartbeat_message(13, 3, 0, 0, 3, 3)
        self.assertFalse(sitl_link.forward_to_sitl(packed(fc_hb, 1, 1)))
        nuc_hb = mav.MAVLink_heartbeat_message(18, 8, 0, 0, 4, 3)
        self.assertTrue(sitl_link.forward_to_sitl(packed(nuc_hb, SITL, 191)))

    def test_sim_params_use_the_x6_pack(self):
        out = sitl_link.sim_params({"maxVoltage": 25.2, "battCapacityAh": 54}, SITL)
        self.assertEqual(out["SYSID_THISMAV"], 2)
        self.assertEqual(out["SIM_BATT_VOLTAGE"], 25.2)
        self.assertEqual(out["SIM_BATT_CAP_AH"], 54)


class BridgeWatchTests(unittest.TestCase):
    def make(self):
        bridge = sitl_link.Bridge.__new__(sitl_link.Bridge)
        bridge.system, bridge.device = SITL, "/dev/cu.usbmodem1"
        bridge.events = []
        bridge.event = lambda text, *a: bridge.events.append(text)
        bridge.clock = lambda: 100.0
        bridge.lock = __import__("threading").Lock()
        bridge.started, bridge.fc_system, bridge.fc_at, bridge.nuc_at, bridge.nuc_system = 0, None, None, None, None
        bridge.counts, bridge._warned = {"to_fc": 0, "to_sitl": 0}, {}
        return bridge

    def test_nuc_on_the_flight_system_id_is_called_out(self):
        bridge = self.make()
        hb = mav.MAVLink_heartbeat_message(18, 8, 0, 0, 4, 3)
        bridge._to_sitl(packed(hb, 1, 191))
        self.assertTrue(any("HERON_SYSTEM_ID=2" in e for e in bridge.events))
        self.assertFalse(bridge.status()["nuc_ok"])

    def test_status_sees_fc_and_nuc(self):
        bridge = self.make()
        bridge._to_sitl(packed(mav.MAVLink_heartbeat_message(13, 3, 0, 0, 3, 3), 1, 1))
        bridge._to_sitl(packed(mav.MAVLink_heartbeat_message(18, 8, 0, 0, 4, 3), SITL, 191))
        st = bridge.status()
        self.assertTrue(st["fc_ok"] and st["nuc_ok"])
        self.assertEqual(st["fc_system"], 1)


class SimServiceTests(unittest.TestCase):
    def test_missing_sitl_build_explains_how_to_build(self):
        sim = sitl_link.SimService({"demo"}, peer=FlightService({"serial"}))
        with mock.patch.object(sitl_link, "ardupilot_dir", return_value=sitl_link.Path("/nonexistent")):
            with self.assertRaisesRegex(FlightError, "waf copter"):
                sim.connect({"transport": "sitl", "home": {"lat": 40.08, "lon": -105.23}})
        self.assertIsNone(sim.link)

    def test_dry_run_needs_a_port_and_frees_it_on_failure(self):
        peer = FlightService({"serial"})
        sim = sitl_link.SimService({"demo"}, peer=peer)
        with self.assertRaisesRegex(FlightError, "USB port"):
            sim.connect({"transport": "dryrun", "home": {"lat": 40.08, "lon": -105.23}})
        with mock.patch.object(sitl_link, "ardupilot_dir", return_value=sitl_link.Path("/nonexistent")):
            with self.assertRaises(FlightError):
                sim.connect({"transport": "dryrun", "device": "/dev/cu.usbmodem1",
                             "home": {"lat": 40.08, "lon": -105.23}})
        self.assertEqual(peer.reserved_ports, set())

    def test_demo_still_works(self):
        sim = sitl_link.SimService({"demo"})
        sim.connect({"transport": "demo"})
        self.assertEqual(sim.snapshot()["telemetry"]["transport"], "demo")
        sim.disconnect()


if __name__ == "__main__":
    unittest.main()
