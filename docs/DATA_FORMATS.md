# DATA_FORMATS — Files, Names, and Layout

Status: Draft 1, 2026-09-16. Most content here is TBD until the
capture code exists. Keep this document current: post-processing
engineers depend on it.

## 1. Raw IQ recordings

- Legacy format (reference): interleaved complex samples, `sc8`
  (1 byte I + 1 byte Q), files split every 30 s, with a metadata
  sidecar, written by `rx_multi_to_file`.
- HERON format: sc8 is confirmed (D-009). Four SDRs record (D-008).
  Byte order, segment interval, per-SDR directory layout, and the
  time-alignment record (Q-004) are TBD — record the final
  definition here when the capture code is adapted.

## 2. Proposed naming (confirm with the team)

`<flight_id>/<channel_id>/<utc_start>_<seq>.<fmt>` with one metadata
JSON per flight that records: git hash, config copy, SDR serials,
UTC start, sample rate, center frequencies, gains, antenna mapping.

## 3. Ground station data

GNSS receiver logs: TBD with Q-002 (receiver choice). Expect RINEX or
receiver-native logs plus a site log (position, antenna height,
times). `Base_Software` also logs telemetry and commands with
timestamps (format TBD when the code exists).

## 4. Logs

Supervisor logs: journald plus rotating files under
`/media/DataStore/logs/`, timestamped UTC.

## 5. Rules

- Never change a format without a decision entry and a converter or
  version marker.
- Every recording must be self-describing through its metadata file.
