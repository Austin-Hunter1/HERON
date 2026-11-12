"""Per-phase auto-record: hover 1, hover 2 and the shoreline passes as separate recordings."""
import unittest

from heron_payload import protocol as P
from heron_payload.controller import check_segments
from tests.test_agent_link import AgentTests, decode
from tests.test_payload import ready

# Monday's science plan: hover 1 = items 4..26, hover 2 = 28..50, passes = 52..122 (nav items every other seq).
PHASES = [[4, 26], [28, 50], [52, 122]]


def fly(c, start, stop):
    """ArduPilot over a run of hold waypoints with a CONDITION_YAW after each one."""
    for seq in range(start, stop + 1, 2):
        c.cube_update(current=seq)
        c.cube_update(reached=seq)        # arrival; the 10 s hold follows
        c.cube_update(current=seq + 1)    # hold done, yaw command
        c.cube_update(current=seq + 2)


def airborne(c):
    c.cube_update(armed=True, mode="AUTO", current=1)


def starts(c):
    return [e["text"] for e in c.events if "REC START" in e["text"]]


class SegmentTests(unittest.TestCase):
    def test_three_phases_record_three_files(self):
        c, _ = ready()
        c.execute("arm_segments", dict(segments=PHASES))
        airborne(c)
        sessions = []
        for a, b in PHASES:
            c.cube_update(current=a)                       # transit: still waiting
            self.assertEqual(c.state, "AUTO_ARMED")
            fly(c, a, b)
            sessions.append(c.session)
        self.assertEqual(c.state, "READY")
        self.assertIsNone(c.plan)
        self.assertEqual(len(set(sessions)), 3)
        self.assertEqual(len(starts(c)), 3)
        self.assertIn("phase 3 of 3", starts(c)[-1])

    def test_hover_keeps_its_last_hold(self):
        c, _ = ready()
        c.execute("arm_segments", dict(segments=PHASES))
        airborne(c)
        c.cube_update(current=4)
        c.cube_update(reached=4)
        self.assertEqual(c.state, "RECORDING")
        c.cube_update(current=26)
        c.cube_update(reached=26)                          # arrival at the last hover point
        self.assertEqual(c.state, "RECORDING")             # still holding there
        c.cube_update(current=27)
        self.assertEqual(c.state, "AUTO_ARMED")            # finished; waiting for hover 2
        self.assertEqual((c.plan["start_seq"], c.plan["stop_seq"]), (28, 50))

    def test_status_reports_the_phase(self):
        c, _ = ready()
        c.execute("arm_segments", dict(segments=PHASES))
        values = P.status_values(c.snapshot())
        self.assertEqual(P.unpack_segment(values[P.F_SEG]), dict(segment=1, segments=3))
        self.assertEqual(P.unpack_auto(values[P.F_AUTO]), dict(start_seq=4, stop_seq=26))

    def test_missed_arrival_starts_late_inside_a_phase(self):
        c, _ = ready()
        c.execute("arm_segments", dict(segments=PHASES))
        airborne(c)
        c.cube_update(current=30)                          # arrival at 28 lost; jumped into hover 2
        self.assertEqual(c.state, "RECORDING")
        self.assertEqual(c.plan["index"], 1)

    def test_arming_mid_mission_skips_finished_phases(self):
        c, _ = ready()
        c.cube_update(armed=True, mode="AUTO", current=40)
        c.execute("arm_segments", dict(segments=PHASES))
        self.assertEqual(c.plan["index"], 1)

    def test_rtl_ends_everything(self):
        c, _ = ready()
        c.execute("arm_segments", dict(segments=PHASES))
        airborne(c)
        c.cube_update(current=4)
        c.cube_update(reached=4)
        c.cube_update(mode="RTL")
        self.assertEqual(c.state, "READY")
        self.assertIsNone(c.plan)

    def test_operator_stop_cancels_the_rest(self):
        c, _ = ready()
        c.execute("arm_segments", dict(segments=PHASES))
        airborne(c)
        c.cube_update(current=4)
        c.cube_update(reached=4)
        c.execute("stop")
        self.assertEqual(c.state, "READY")
        self.assertIsNone(c.plan)

    def test_single_range_arm_is_unchanged(self):
        c, _ = ready()
        c.execute("arm", dict(start_seq=4, stop_seq=122))
        airborne(c)
        c.cube_update(reached=4)
        fly(c, 4, 120)
        self.assertEqual(c.state, "RECORDING")
        c.cube_update(reached=122)
        self.assertEqual(c.state, "READY")

    def test_bad_segments(self):
        for bad in ([], [[5, 4]], [[4, 26], [20, 30]], [[0, 3]], [[1, 2]] * 5, [["a", 2]]):
            with self.assertRaises(ValueError, msg=bad):
                check_segments(bad)
        self.assertEqual(check_segments([[4, 4], [6, 9]]), [[4, 4], [6, 9]])

    def test_wire_packing_round_trips(self):
        params = P.segment_params(PHASES)
        self.assertEqual(P.segments_from_params(*params), PHASES)
        self.assertEqual(P.segments_from_params(*P.segment_params([[4, 26]])), [[4, 26]])


class AgentSegmentTests(AgentTests):
    def test_agent_arms_segments_from_real_mavlink(self):
        p2, p3, p5, p6, p7 = P.segment_params(PHASES)
        m = decode(self.gcs, "command_long_send", 1, 191, P.COMMAND, 0, P.ACTION_ARM_SEGMENTS, p2, p3, 88, p5, p6, p7)
        self.agent.receive(m)
        self.assertEqual(self.controller.state, "AUTO_ARMED")
        self.assertEqual(self.controller.plan["segments"], PHASES)
        ack = [x for x in self.down.out if x.get_type() == "COMMAND_ACK"][-1]
        self.assertEqual((ack.result, ack.result_param2), (P.RESULT_ACCEPTED, 88))


if __name__ == "__main__":
    unittest.main()
