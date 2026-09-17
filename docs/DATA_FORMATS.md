# DATA_FORMATS — Files, Names, and Layout

Status: Draft 2, 2026-09-17. This is the format the capture code
writes. Keep this document current: post-processing engineers depend
on it. The C++ recorder is not yet run on hardware; confirm the
layout on the bench and update the status line here.

## 1. Raw IQ recordings (D-009, D-021)

Directory layout, one flight per directory under `disk.data_root`
(default `/media/DataStore/iq`):

```
<flight_id>/
  metadata.json
  <sdr_id>/<channel_id>/<utc_start>_<seq>.sc8
  <sdr_id>/<channel_id>/<utc_start>_<seq>.sc8.json     (sidecar)
```

- `flight_id`: the name in the START command, or the UTC start stamp
  `YYYYMMDDTHHMMSSZ` when none was given; optional config prefix.
  Only letters, digits, `_`, `-`.
- `sdr_id`, `channel_id`: from `onboard.toml` (`[[sdr]]` and
  `[[sdr.channels]]`), for example `b210_1/L5_direct`.
- `utc_start`: the flight start stamp, the same for every file of the
  flight. `seq`: five-digit segment number from `00000`.
- Sample format: `sc8`, interleaved I then Q, one signed byte each,
  little-endian, one file per channel. (`sc16` is possible in config:
  two signed 16-bit little-endian values per sample.)
- Segments: fixed sample count = `segment_seconds × sample_rate`
  (default 30 s). Segments cut on the sample count, so file boundaries
  align to device time. The last segment of a recording is shorter and
  its sidecar has `"complete": false`. A power loss corrupts at most
  the open segment (O16).

### Segment sidecar (`<file>.json`)

| Key | Meaning |
| --- | --- |
| `first_sample_device_time` | USRP time (seconds) of the first sample. For PPS-aligned units this is UTC. −1 when no timestamp was available. |
| `first_sample_host_time` | Host UNIX time when the file was opened (coarse). |
| `num_samples` | Samples in this file. |
| `sample_rate_hz`, `center_freq_hz`, `gain_db`, `bandwidth_hz`, `antenna` | Channel settings in use. |
| `overflows_in_segment`, `host_drops_in_segment` | Loss counters inside this segment (see section 4). |
| `complete` | `true` for a full-length segment. |

### Flight metadata (`metadata.json`, O5)

Written at start and rewritten at stop. Holds: `flight_id`,
`software_version` (git hash), `start_utc`/`stop_utc`,
`start_reason`/`stop_reason`, the full config copy, per-SDR serial
numbers and channel settings, the timing scheme (`sync_epoch_unix`,
`stream_start_unix`, per-SDR `clock_source`/`time_source`), and
per-SDR totals (samples, overflows, host drops, segments, fault).

## 2. Time base (D-020, Q-013)

- All four units lock to one 10 MHz reference, so their sample clocks
  do not drift against each other.
- The B210s also take PPS. Every B210 recorder sets its device time to
  `sync_epoch_unix + 1` at the PPS edge of that second and starts
  streaming at `stream_start_unix`. Their `first_sample_device_time`
  values are therefore on one UTC time base, to within one sample
  clock.
- The B200minis have no PPS. Their recorders set device time from the
  host clock and start at the same `stream_start_unix`. Their offset
  to the B210 streams is a **constant** (shared 10 MHz) known to
  host-clock accuracy (milliseconds). Find the exact value once per
  flight after the fact (Q-013), for example by correlating a direct
  channel against a B210 direct channel.
- The host clock only needs to be within ±0.5 s of UTC for the
  absolute labels to be right. Relative alignment among PPS units does
  not depend on the host clock at all.

## 3. Ground station data (B4, B5)

- `heron_base_logs/flights/base_<stamp>.jsonl`: one JSON object per
  line with `t` (UTC ISO), `kind` (`tlm`, `cmd`, `ack`, `event`) and
  the message fields. A flight can be replayed from this file.
- `heron_base_logs/gnss/gnss_<stamp>.ubx`: the raw receiver byte
  stream (UBX + NMEA + RTCM3 as the receiver sends it). Convert with
  RTKLIB `rtkconv` for PPK, as SURGE did.
- Site log (position, antenna height, times): keep by hand until the
  receiver choice is final (Q-002).

## 4. Loss counters

- `overflows`: UHD reported an overflow (samples lost between the
  device and the host, USB or device FIFO). Zero is the pass condition
  in `TEST_BENCH_ROUTINE.md`.
- `host_drops`: the recorder's host ring buffer was full (the disk fell
  behind) and a 10 ms chunk was discarded. Must be zero in flight.

## 5. Logs

Supervisor logs: journald plus rotating files under
`/media/DataStore/logs/heron-onboard.log`, timestamped UTC.

## 6. Rules

- Never change a format without a decision entry and a converter or
  version marker (`metadata_version` in `metadata.json`).
- Every recording must be self-describing through its metadata file.
