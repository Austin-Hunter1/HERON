## this script relays the RTK correction data from a base station to the autopilot

import argparse
import collections
import json
import logging
import socket
import sys
import time

# log output
log = logging.getLogger("RTCM relay")

# split RTCM messages into smaller fragments
MAX_FRAG = 180
MAX_FRAGS = 4

# values copied from MAVLink common message set
MAV_TYPE_GCS = 6            # ground control station
MAV_AUTOPILOT_INVALID = 8   # not an autopilot
# strings for the MAVLink GPS_FIX_TYPE
FIX_NAMES = {0: "NO_GPS", 1: "NO_FIX", 2: "2D", 3: "3D",
             4: "DGPS", 5: "RTK_FLOAT", 6: "RTK_FIXED"}


# ---------------------------------------------------------------------------
# RTCM3 framing
# ---------------------------------------------------------------------------

# compute the CRC-24Q checksum that RTCM3 uses to protect each frame (bitwise function)
def crc24q(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 16               
        for _ in range(8):              
            crc <<= 1
            if crc & 0x1000000:         
                crc ^= 0x1864CFB        
    return crc & 0xFFFFFF               

# read raw byte stream
class RtcmFramer:
    """
    RTCM3 frames:
        byte 0        : 0xD3 (preamble, marks the start of a frame)
        bytes 1-2     : 6 reserved bits (always 0) + 10-bit payload length
        next `length` : payload (first 12 bits = the RTCM message number)
        last 3 bytes  : CRC-24Q over everything before it
    """

    def __init__(self):
        self.buf = bytearray()   
        self.crc_errors = 0      

    # return list of any complete valid frames from new bytes
    def feed(self, data: bytes):
        self.buf.extend(data)
        frames = []

        while True:  # finding preamble
            i = self.buf.find(b"\xd3")
            if i < 0:
                self.buf.clear()         
                break
            if i > 0:
                del self.buf[:i]          

            if len(self.buf) < 3:
                break

            if self.buf[1] & 0xFC:
                del self.buf[:1]
                continue

            length = ((self.buf[1] & 0x03) << 8) | self.buf[2]
            total = 3 + length + 3        # header + payload + CRC

            if len(self.buf) < total:
                break

            frame = bytes(self.buf[:total])
            expected = int.from_bytes(frame[-3:], "big")
            if crc24q(frame[:-3]) == expected:
                frames.append(frame)      
                del self.buf[:total]      
            else:
                self.crc_errors += 1
                del self.buf[:1]

        return frames


def rtcm_type(frame: bytes) -> int:
    return (frame[3] << 4) | (frame[4] >> 4) # return the message # of a frame


# ---------------------------------------------------------------------------
# MAVLink fragmentation
# ---------------------------------------------------------------------------

# split rtcm frame into flags and payload for GPS_RTCM_DATA
def fragment(frame: bytes, seq: int):
    """
    flags byte:
        bit 0      : 1 = this message is fragmented, 0 = it fits in one packet
        bits 1-2   : fragment number (0..3), so the autopilot can reassemble in order
        bits 3-7   : 5-bit sequence number, same for all fragments of one RTCM
                     message, so the autopilot can detect mixed-up fragments

    raises ValueError if frame is > 720 bytes
    """
    seq &= 0x1F   

    if len(frame) < MAX_FRAG:
        return [(seq << 3, frame)]

    chunks = [frame[i:i + MAX_FRAG] for i in range(0, len(frame), MAX_FRAG)]

    if len(chunks) > MAX_FRAGS:
        raise ValueError(f"RTCM frame of {len(frame)} bytes exceeds {MAX_FRAG * MAX_FRAGS}")

    if len(chunks[-1]) == MAX_FRAG and len(chunks) < MAX_FRAGS:
        chunks.append(b"")

    return [(1 | (i << 1) | (seq << 3), c) for i, c in enumerate(chunks)]


# ---------------------------------------------------------------------------
# Relay
# ---------------------------------------------------------------------------

# reads RTCM3 and fwd it over mavlink
class RtcmRelay:

    def __init__(self, base_serial, mav, send_heartbeat=True,
                 read_mavlink=True, on_position=None):
        self.base = base_serial
        self.mav = mav
        self.send_heartbeat = send_heartbeat
        self.read_mavlink = read_mavlink
        self.on_position = on_position

        self.framer = RtcmFramer()
        self.seq = 0                                 
        self.frames_sent = 0                        
        self.bytes_sent = 0                         
        self.oversize_drops = 0                      
        self.type_counts = collections.Counter()     
        self.fix = None                             
        self.last_position = None                    
        self._t_heartbeat = 0.0                      

    # -- base station -> MAVLink -------------------------------------------

    # poll base station and read/forward message
    def poll_base(self) -> int:
        waiting = self.base.in_waiting
        if not waiting:
            return 0                                 # nothing to do; don't block the caller

        sent = 0
        for frame in self.framer.feed(self.base.read(waiting)):
            try:
                parts = fragment(frame, self.seq)
            except ValueError:
                self.oversize_drops += 1
                log.warning("dropping oversize RTCM message type %d (%d bytes)",
                            rtcm_type(frame), len(frame))
                continue

            for flags, payload in parts:
                padded = payload + bytes(MAX_FRAG - len(payload))
                self.mav.mav.gps_rtcm_data_send(flags, len(payload), list(padded))

            self.seq = (self.seq + 1) & 0x1F        
            self.frames_sent += 1
            self.bytes_sent += len(frame)
            self.type_counts[rtcm_type(frame)] += 1
            sent += 1
        return sent

    # -- MAVLink -> position read-back -------------------------------------

    # process incoming MAVLink message using GPS_RAW_INT - fix type lets us know if RTK is working
    def handle_mavlink_message(self, msg):
        if msg is None or msg.get_type() != "GPS_RAW_INT":
            return

        self.fix = msg.fix_type
        self.last_position = {
            "time": time.time(),
            "fix": FIX_NAMES.get(msg.fix_type, msg.fix_type),
            "lat": msg.lat / 1e7,                    
            "lon": msg.lon / 1e7,
            "alt_m": msg.alt / 1000.0,
            "h_acc_m": getattr(msg, "h_acc", 0) / 1000.0,
            "v_acc_m": getattr(msg, "v_acc", 0) / 1000.0,
            "sats": msg.satellites_visible,
        }
        if self.on_position:
            self.on_position(self.last_position)

    def service(self):
        """Housekeeping: optional heartbeat, and optional draining of the link."""
        now = time.time()
        if self.send_heartbeat and now - self._t_heartbeat >= 1.0:
            # Announce ourselves as a GCS so the autopilot (and any MAVLink
            # router in between) knows we exist and routes our messages.
            self.mav.mav.heartbeat_send(MAV_TYPE_GCS, MAV_AUTOPILOT_INVALID, 0, 0, 0)
            self._t_heartbeat = now

        if self.read_mavlink:
            # Drain everything waiting so the receive buffer can't back up.
            while True:
                msg = self.mav.recv_match(blocking=False)
                if msg is None:
                    break
                self.handle_mavlink_message(msg)

    def step(self):
        self.poll_base()
        self.service()

    # -- diagnostics ---------------------------------------------------------

    def status(self) -> str: # periodic logging summary
        return (f"frames={self.frames_sent} bytes={self.bytes_sent} "
                f"crc_err={self.framer.crc_errors} oversize_drops={self.oversize_drops} "
                f"types={dict(sorted(self.type_counts.items()))} "
                f"drone_fix={FIX_NAMES.get(self.fix, self.fix)}")


# ---------------------------------------------------------------------------
# Command-line entry point (only used when run as a script)
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-port", required=True, help="serial port of the base F9P")
    ap.add_argument("--base-baud", type=int, default=115200)
    ap.add_argument("--mav", required=True,
                    help="pymavlink connection string (serial port, udpout:host:port, ...)")
    ap.add_argument("--mav-baud", type=int, default=57600)
    ap.add_argument("--pos-out", metavar="HOST:PORT",
                    help="send the drone's GPS position as JSON lines over UDP")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    import serial
    from pymavlink import mavutil

    ser = serial.Serial(args.base_port, args.base_baud, timeout=0.1)

    master = mavutil.mavlink_connection(args.mav, baud=args.mav_baud,
                                        source_system=255, source_component=190)

    # Optional: forward every position update as a JSON line over UDP.
    on_position = None
    if args.pos_out:
        host, port = args.pos_out.rsplit(":", 1)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        addr = (host, int(port))
        on_position = lambda pos: sock.sendto((json.dumps(pos) + "\n").encode(), addr)

    relay = RtcmRelay(ser, master, on_position=on_position)

    log.info("Relaying RTCM3 -> MAVLink. Ctrl-C to stop.")
    t_status = 0.0
    try:
        while True:
            relay.step()
            if time.time() - t_status >= 5.0:
                log.info(relay.status())
                t_status = time.time()
            time.sleep(0.005)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())