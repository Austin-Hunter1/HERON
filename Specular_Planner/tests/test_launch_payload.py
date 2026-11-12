"""Launch arms the payload with the plan's recording, checks what the payload reports back, and
refuses to arm the motors when it can't (unless the operator chose to fly without recording)."""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "HERON_Payload"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.flight import FlightService  # noqa: E402
from engine.payload import SimPayload, arm_for_launch, payload_ready, recording_plan  # noqa: E402
from test_payload_integration import science_plan  # noqa: E402


def finish(service, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if service.job["state"] != "running":
            return service.job
        time.sleep(0.02)
    raise AssertionError("Flight operation did not finish")


class Offline:
    """A payload that never answers."""
    def status(self):
        return {"connected": False, "state": "OFFLINE", "auto": None}

    def command(self, *a, **k):
        raise ValueError("No signal from the payload.")


class OldNuc(SimPayload):
    def command(self, action, start_seq=None, stop_seq=None, segments=None):
        if action == "arm_segments":
            raise ValueError("The NUC's payload software is older than this planner.")
        return super().command(action, start_seq, stop_seq, segments=segments)


class LaunchPayloadTests(unittest.TestCase):
    def setUp(self):
        self.service = FlightService()
        self.service.connect({"transport": "demo"})
        self.plan_id = science_plan(self.service)
        self.payload = SimPayload(FlightService())     # a recorder that does not follow this aircraft
        self.events = []
        real_event = self.service.event
        self.service.event = lambda text, *a, **k: (self.events.append(text), real_event(text, *a, **k))
        self.hook(self.payload)

    def tearDown(self):
        self.payload.closed.set()

    def hook(self, payload):
        self.service.payload_ready = lambda pid: payload_ready(payload, self.service, pid)
        self.service.payload_arm = lambda pid, verify_only=False: arm_for_launch(payload, self.service, pid, verify_only, timeout_s=2)

    def upload(self):
        self.service.submit("upload", self.plan_id)
        self.assertEqual(finish(self.service)["state"], "complete")

    def meta(self):
        return self.service.plans[self.plan_id]["meta"]

    def test_launch_arms_each_recorded_phase_before_the_motors(self):
        self.upload()
        self.service.submit("launch", self.plan_id, "LAUNCH", wait_until_start=False)
        self.assertEqual(finish(self.service)["state"], "complete", self.service.job)
        self.assertEqual(self.payload.controller.plan["segments"], [[2, 6], [8, 12], [14, 20]])
        armed = next(i for i, e in enumerate(self.events) if e.startswith("Payload armed and confirmed"))
        motors = next(i for i, e in enumerate(self.events) if e.startswith("Arming aircraft"))
        self.assertLess(armed, motors)

    def test_unticked_phase_is_left_out(self):
        self.meta()["survey"]["phases"][1]["record"] = False
        self.assertEqual(recording_plan(self.service, self.plan_id)["segments"], [[2, 6], [14, 20]])
        self.upload()
        self.service.submit("launch", self.plan_id, "LAUNCH", wait_until_start=False)
        self.assertEqual(finish(self.service)["state"], "complete", self.service.job)
        self.assertEqual(self.payload.controller.plan["segments"], [[2, 6], [14, 20]])

    def test_one_file_mode_arms_a_single_range(self):
        self.meta()["recording_mode"] = "continuous"
        self.upload()
        self.service.submit("launch", self.plan_id, "LAUNCH", wait_until_start=False)
        self.assertEqual(finish(self.service)["state"], "complete", self.service.job)
        plan = self.payload.controller.plan
        self.assertEqual((plan["start_seq"], plan["stop_seq"], "segments" in plan), (2, 20, False))

    def test_nothing_ticked_needs_no_payload(self):
        for ph in self.meta()["survey"]["phases"]:
            ph["record"] = False
        self.hook(Offline())
        self.upload()
        self.service.submit("launch", self.plan_id, "LAUNCH", wait_until_start=False)
        self.assertEqual(finish(self.service)["state"], "complete", self.service.job)

    def test_silent_payload_blocks_launch_and_motors_stay_off(self):
        self.hook(Offline())
        self.upload()
        check = next(c for c in self.service.preflight(self.plan_id)["checks"] if c["key"] == "payload")
        self.assertFalse(check["ok"])
        self.assertIn("no signal from the NUC", check["message"])
        self.service.submit("launch", self.plan_id, "LAUNCH", wait_until_start=False)
        job = finish(self.service)
        self.assertEqual(job["state"], "error")
        self.assertIn("Payload", job["message"])
        self.assertFalse(self.service.link.armed)

    def test_fly_without_recording_is_one_launch_only(self):
        self.hook(Offline())
        self.upload()
        self.service.submit("launch", self.plan_id, "LAUNCH", wait_until_start=False, without_recording=True)
        self.assertEqual(finish(self.service)["state"], "complete", self.service.job)
        self.assertTrue(any("without payload recording" in e for e in self.events))
        self.service.submit("refresh")
        finish(self.service)
        self.assertFalse(self.service.without_recording)

    def test_arm_at_launch_off_ignores_a_missing_payload(self):
        self.hook(Offline())
        self.service.set_check("payload", False)          # the strip's "Arm automatically at launch" box
        self.upload()
        self.service.submit("launch", self.plan_id, "LAUNCH", wait_until_start=False)
        self.assertEqual(finish(self.service)["state"], "complete", self.service.job)

    def test_old_nuc_refusal_stops_the_launch(self):
        old = OldNuc(FlightService())
        try:
            self.hook(old)
            self.upload()
            self.service.submit("launch", self.plan_id, "LAUNCH", wait_until_start=False)
            job = finish(self.service)
            self.assertEqual(job["state"], "error")
            self.assertIn("refused to arm", job["message"])
            self.assertFalse(self.service.link.armed)
        finally:
            old.closed.set()

    def test_payload_that_does_not_report_the_plan_back_stops_the_launch(self):
        class Forgetful(SimPayload):
            def status(self):
                out = super().status()
                out["auto"] = None                       # accepted, but reports nothing armed
                return out
        bad = Forgetful(FlightService())
        try:
            self.hook(bad)
            self.upload()
            self.service.submit("launch", self.plan_id, "LAUNCH", wait_until_start=False)
            job = finish(self.service)
            self.assertEqual(job["state"], "error")
            self.assertIn("did not confirm", job["message"])
            self.assertFalse(self.service.link.armed)
        finally:
            bad.closed.set()

    def test_payload_is_armed_before_the_countdown_and_rearmed_if_it_dropped(self):
        from datetime import datetime, timedelta, timezone
        self.meta()["start"] = (datetime.now(timezone.utc) + timedelta(seconds=40)).isoformat()
        self.upload()
        self.service.submit("launch", self.plan_id, "LAUNCH", wait_until_start=True)
        deadline = time.monotonic() + 15
        while self.payload.controller.state != "AUTO_ARMED" and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(self.payload.controller.state, "AUTO_ARMED")   # armed while still counting down
        self.assertFalse(self.service.link.armed)
        self.payload.command("cancel_auto")                              # e.g. the NUC restarted
        self.service.skip_wait()
        self.assertEqual(finish(self.service)["state"], "complete", self.service.job)
        self.assertEqual(self.payload.controller.plan["segments"], [[2, 6], [8, 12], [14, 20]])

    def test_rearms_when_armed_for_something_else(self):
        self.payload.command("arm", 2, 4)
        self.upload()
        self.service.submit("launch", self.plan_id, "LAUNCH", wait_until_start=False)
        self.assertEqual(finish(self.service)["state"], "complete", self.service.job)
        self.assertEqual(self.payload.controller.plan["segments"], [[2, 6], [8, 12], [14, 20]])


if __name__ == "__main__":
    unittest.main()
