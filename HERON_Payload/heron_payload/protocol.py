"""HERON payload <-> ground station protocol, using only standard MAVLink messages.

Ground -> payload
    COMMAND_LONG to (vehicle system id, component 191), command MAV_CMD_USER_1 (31010)
        param1  action (see ACTION_*)
        param2  start mission seq   (ARM_AUTO only)
        param3  stop mission seq    (ARM_AUTO only)
        param4  request id 1..16777215; a retry reuses it so the payload acts once
        ARM_SEGMENTS (record each science phase separately, up to MAX_SEGMENTS):
        param2/param3  first segment's start/stop mission seq
        param5..7      further segments packed as start*1000 + stop (0 = none)
        A segment records from arrival at its start item until its stop item is finished
        (the aircraft moves on), so a hover's last hold is kept.
    The payload answers with COMMAND_ACK (command 31010, result_param2 = request id).
    A refusal also produces a STATUSTEXT "HERON: <reason>".

Payload -> ground (through the Cube's MAVLink routing)
    HEARTBEAT            1 Hz, MAV_TYPE_ONBOARD_CONTROLLER, component 191
    NAMED_VALUE_FLOAT    1 Hz, the STATUS_FIELDS below
    STATUSTEXT           on every state change, e.g. "HERON REC START wp4"
                         plus "HERON setup: <name>" when the setup changes or on request

Everything also shows up in Mission Planner / QGroundControl without a plugin.
"""

COMPONENT = 191            # MAV_COMP_ID_ONBOARD_COMPUTER
COMMAND = 31010            # MAV_CMD_USER_1

ACTION_STOP = 0
ACTION_START = 1
ACTION_ARM_AUTO = 2
ACTION_CANCEL_AUTO = 3
ACTION_STATUS = 4          # re-send setup name / fault text now
ACTION_ARM_SEGMENTS = 5
MAX_SEGMENTS = 4

ACTIONS = {ACTION_STOP: "stop", ACTION_START: "start", ACTION_ARM_AUTO: "arm",
           ACTION_CANCEL_AUTO: "cancel_auto", ACTION_STATUS: "status", ACTION_ARM_SEGMENTS: "arm_segments"}
ACTION_CODES = {name: code for code, name in ACTIONS.items()}

# MAV_RESULT values used in COMMAND_ACK
RESULT_ACCEPTED = 0
RESULT_DENIED = 2
RESULT_FAILED = 4

STATES = ["OFFLINE", "IDLE", "READY", "ARMED", "RECORDING", "FAULT", "PREPARING", "FINALIZING"]
STATE_CODES = {name: code for code, name in enumerate(STATES)}
# Controller state names -> wire names
CONTROLLER_TO_WIRE = {"IDLE": "IDLE", "READY": "READY", "AUTO_ARMED": "ARMED", "RECORDING": "RECORDING",
                      "FAULT": "FAULT", "PREPARING": "PREPARING", "FINALIZING": "FINALIZING"}

# NAMED_VALUE_FLOAT names (max 10 characters)
F_STATE = "HRN_STATE"     # STATES index
F_ELAPSED = "HRN_REC_S"   # seconds in the current recording
F_RATE = "HRN_MBPS"       # MB/s written to disk
F_LEFT = "HRN_MINLFT"     # minutes of recording left on disk (-1 unknown)
F_FREE = "HRN_FREEGB"     # GB free on the data disk
F_OVF = "HRN_OVF"         # receive overflows + gaps this recording
F_AUTO = "HRN_AUTO"       # start*1000 + stop while auto-record is armed or active, else 0
F_SEG = "HRN_SEG"         # segment number*10 + segment count with per-phase auto-record, else 0
F_CPU = "HRN_CPU_C"       # NUC CPU package temperature, deg C (-1 unknown)
F_SSD = "HRN_SSD_C"       # NUC data SSD temperature, deg C (-1 unknown)
STATUS_FIELDS = (F_STATE, F_ELAPSED, F_RATE, F_LEFT, F_FREE, F_OVF, F_AUTO, F_SEG, F_CPU, F_SSD)

SETUP_PREFIX = "HERON setup: "
TEXT_PREFIX = "HERON"


def pack_auto(plan):
    if not plan:
        return 0.0
    return float(int(plan["start_seq"]) * 1000 + int(plan["stop_seq"]))


def pack_segment(start, stop):
    return float(int(start) * 1000 + int(stop))


def segment_params(segments):
    """[[start, stop], ...] -> COMMAND_LONG param2, param3, param5, param6, param7."""
    segs = [list(map(int, s)) for s in segments]
    if not 1 <= len(segs) <= MAX_SEGMENTS:
        raise ValueError(f"Auto-record takes 1 to {MAX_SEGMENTS} segments.")
    extra = [pack_segment(*s) for s in segs[1:]] + [0.0] * (MAX_SEGMENTS - len(segs))
    return float(segs[0][0]), float(segs[0][1]), extra[0], extra[1], extra[2]


def segments_from_params(p2, p3, p5, p6, p7):
    segs = [[int(round(p2)), int(round(p3))]]
    for v in (p5, p6, p7):
        v = int(round(v or 0))
        if v > 0:
            segs.append([v // 1000, v % 1000])
    return segs


def unpack_segment(value):
    value = int(round(value or 0))
    if value <= 0:
        return None
    return dict(segment=value // 10, segments=value % 10)


def unpack_auto(value):
    value = int(round(value or 0))
    if value <= 0:
        return None
    return dict(start_seq=value // 1000, stop_seq=value % 1000)


def status_values(snapshot):
    """Controller snapshot -> {NAMED_VALUE_FLOAT name: value}."""
    overflows = sum(int(r.get("overflows", 0)) + int(r.get("gaps", 0)) for r in snapshot.get("radios", []))
    left = snapshot.get("remaining_minutes")
    return {
        F_STATE: float(STATE_CODES[CONTROLLER_TO_WIRE.get(snapshot["state"], "FAULT")]),
        F_ELAPSED: float(snapshot.get("elapsed_s") or 0),
        F_RATE: float(snapshot.get("rate_mbps") or 0),
        F_LEFT: float(-1 if left is None else left),
        F_FREE: float(snapshot.get("disk_free_gb") or 0),
        F_OVF: float(overflows),
        F_AUTO: pack_auto(snapshot.get("plan")),
        F_SEG: _pack_seg(snapshot.get("plan")),
        F_CPU: _temp(snapshot, "cpu_c"),
        F_SSD: _temp(snapshot, "ssd_c"),
    }


def _temp(snapshot, key):
    value = (snapshot.get("temps") or {}).get(key)
    return float(-1 if value is None else value)


def _pack_seg(plan):
    if not plan or "segments" not in plan:
        return 0.0
    return float((plan["index"] + 1) * 10 + len(plan["segments"]))


# ArduCopter custom_mode numbers
COPTER_MODES = {0: "STABILIZE", 1: "ACRO", 2: "ALT_HOLD", 3: "AUTO", 4: "GUIDED", 5: "LOITER", 6: "RTL",
                7: "CIRCLE", 9: "LAND", 11: "DRIFT", 13: "SPORT", 14: "FLIP", 15: "AUTOTUNE", 16: "POSHOLD",
                17: "BRAKE", 18: "THROW", 19: "AVOID_ADSB", 20: "GUIDED_NOGPS", 21: "SMART_RTL", 22: "FLOWHOLD",
                23: "FOLLOW", 24: "ZIGZAG", 25: "SYSTEMID", 26: "AUTOROTATE", 27: "AUTO_RTL"}
