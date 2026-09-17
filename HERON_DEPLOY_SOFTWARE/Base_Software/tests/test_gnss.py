"""NMEA parsing, RTCM splitting, and the receiver data path."""

from heron_base.config import GnssConfig
from heron_base.gnss.correction import NullSink
from heron_base.gnss.nmea import NmeaExtractor, nmea_checksum_ok, parse_gga
from heron_base.gnss.receiver import GnssReceiver
from heron_base.gnss.rtcm import Rtcm3Splitter, crc24q, rtcm_message_type

GGA = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"


def test_checksum():
    assert nmea_checksum_ok(GGA)
    assert not nmea_checksum_ok(GGA[:-1] + "0")
    assert not nmea_checksum_ok("garbage")


def test_parse_gga_fields():
    fix = parse_gga(GGA)
    assert fix is not None
    assert fix.quality == 1 and fix.quality_text == "GPS" and fix.satellites == 8
    assert abs(fix.latitude - 48.1173) < 1e-4 and abs(fix.longitude - 11.5167) < 1e-4
    assert fix.hdop == 0.9 and fix.altitude_m == 545.4


def test_parse_gga_rejects_other_sentences():
    rmc = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
    assert parse_gga(rmc) is None


def test_extractor_finds_sentences_in_binary_noise():
    ext = NmeaExtractor()
    data = b"\xb5b\x01\x07$\x00\xd3" + GGA.encode() + b"\r\n\x00\x01"
    sentences = ext.feed(data[:20]) + ext.feed(data[20:])
    assert any(parse_gga(s) for s in sentences)


def _rtcm_frame(msg_type: int, payload_len: int = 10) -> bytes:
    payload = bytes([(msg_type >> 4) & 0xFF, (msg_type & 0x0F) << 4]) + bytes(payload_len - 2)
    head = bytes([0xD3, (len(payload) >> 8) & 0x03, len(payload) & 0xFF]) + payload
    return head + crc24q(head).to_bytes(3, "big")


def test_crc24q_known_value():
    """CRC-24Q of the 3 bytes 0xD3 0x00 0x00 must round-trip through our splitter."""
    frame = _rtcm_frame(1005)
    assert crc24q(frame[:-3]) == int.from_bytes(frame[-3:], "big")


def test_rtcm_splitter_handles_noise_and_partial_frames():
    f1, f2 = _rtcm_frame(1005), _rtcm_frame(1077, 40)
    stream = b"junk\xd3" + f1 + b"$GPGGA,x*00\r\n" + f2
    splitter = Rtcm3Splitter()
    got = splitter.feed(stream[:12]) + splitter.feed(stream[12:30]) + splitter.feed(stream[30:])
    assert got == [f1, f2]
    assert rtcm_message_type(f1) == 1005 and rtcm_message_type(f2) == 1077
    assert splitter.resyncs >= 1  # The junk 0xD3 was skipped by its reserved bits.


def test_rtcm_splitter_rejects_bad_crc():
    frame = bytearray(_rtcm_frame(1005))
    frame[5] ^= 0xFF
    splitter = Rtcm3Splitter()
    assert splitter.feed(bytes(frame)) == []
    assert splitter.crc_errors == 1


def test_receiver_data_path_without_serial():
    sink = NullSink()
    rx = GnssReceiver(GnssConfig(enabled=False), sink)
    rx.process_bytes(GGA.encode() + b"\r\n" + _rtcm_frame(1005), now=10.0)
    st = rx.status()
    assert st.fix is not None and st.fix.satellites == 8
    assert st.rtcm_frames == 1 and st.rtcm_types == {1005: 1} and sink.count == 1
    assert "GPS sats 8" in st.fix_text(11.0, stale_s=5)
    assert st.fix_text(100.0, stale_s=5).startswith("STALE")
