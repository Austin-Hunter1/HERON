# heron_recorder — the C++ capture path

One process records one SDR to segment files. The Python supervisor
(`heron-onboard`) starts one process per SDR and reads its JSON status
lines. See the header comment in `heron_recorder.cpp` for the design.

**Status: written against the UHD 4.x API. Not yet compiled or run on
hardware.** Build it on the NUC, then run `TEST_BENCH_ROUTINE.md`
section 3 before any flight.

## Dependencies (Ubuntu 22.04 / 24.04)

```bash
sudo apt install -y build-essential cmake libuhd-dev uhd-host libboost-program-options-dev
sudo uhd_images_downloader
```

UHD 4.x from apt supports the B210 and the B200mini/B206mini (D-020).
Check the version: `uhd_config_info --version` (4.1 or newer).

## Build and install

```bash
cd HERON_DEPLOY_SOFTWARE/Onboard_Software/recorder
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
sudo cmake --install build
```

The binary lands in `/usr/local/bin/heron_recorder`, which is the
default `capture.recorder_binary` in the onboard config.

## Run by hand (bench)

```bash
heron_recorder --args serial=XXXXXXX --rate 22e6 \
  --clock-source internal --time-source none \
  --outdir /media/DataStore/iq/bench --file-prefix test --sdr-id b210_1 \
  --channel "index=0,id=L5_direct,freq=1176.45e6,gain=45,bw=20.322e6,antenna=RX2"
```

Stop it with Ctrl-C (SIGINT) or `kill -TERM`. It closes the current
segment, writes its sidecar, and prints `{"event":"stopped",...}`.

## Output layout

```
<outdir>/<sdr-id>/<channel-id>/<prefix>_00000.sc8
<outdir>/<sdr-id>/<channel-id>/<prefix>_00000.sc8.json   (sidecar)
```

Samples are interleaved I,Q, one byte each for `sc8`, little-endian.
The sidecar holds the device time of the first sample, the sample
count, and the overflow count inside that segment.

## Time alignment (D-020)

With `--time-source external` and `--sync-epoch T`, the process waits
for the PPS edge of UNIX second `T`, sets the device time to `T+1` at
that edge, and starts streaming at `--start-time`. Every recorder of
one flight gets the same `T` and start time, so all files share one
time base. The host clock only needs to be within ±0.5 s of UTC for
the labels to be absolute; the relative alignment does not depend on
the host clock at all.
