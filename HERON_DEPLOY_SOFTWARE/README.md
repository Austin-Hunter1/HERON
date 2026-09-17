# HERON_DEPLOY_SOFTWARE

All new HERON code. Three packages in one `uv` workspace:

| Package | Directory | Runs on | Command |
| --- | --- | --- | --- |
| `heron-common` | `Common_Software/` | both | (library: config, protocol, transports) |
| `heron-onboard` | `Onboard_Software/` | payload NUC | `heron-onboard` |
| `heron-base` | `Base_Software/` | ground laptop | `heron-base` |

Plus the C++ recorder in `Onboard_Software/recorder/` (payload only).

## Dependencies

### Every machine (payload and ground)

| Dependency | Version | Purpose | Install |
| --- | --- | --- | --- |
| Python | 3.11 or newer | all orchestration code | `uv python install 3.12` if the OS has none |
| `uv` | 0.5+ | package manager, lock file, venv | `curl -LsSf https://astral.sh/uv/install.sh \| sh` (Linux/macOS), `winget install astral-sh.uv` (Windows) |
| `pydantic` | 2.6+ | config and message validation | `uv sync` |
| `pymavlink` | 2.4.40+ | MAVLink TUNNEL transport (flight link, D-016) | `uv sync` |
| `pyserial` | 3.5+ | serial transport, GNSS receiver | `uv sync` |
| `psutil` | 5.9+ | CPU load, temperatures (onboard) | `uv sync` |
| `textual` | 0.70+ | terminal display (base) | `uv sync` |
| `pytest`, `ruff`, `mypy` | latest | tests and static checks (dev) | `uv sync` |

`uv sync --all-packages` installs all of the above from `uv.lock`.

### Payload NUC only (Ubuntu Server 22.04 / 24.04)

| Dependency | Version | Purpose | Install |
| --- | --- | --- | --- |
| UHD | 4.x (`libuhd-dev`, `uhd-host`) | USRP driver for B210 and B200mini | `sudo apt install uhd-host libuhd-dev` then `sudo uhd_images_downloader` |
| CMake, g++ | CMake 3.16+, C++17 compiler | build the recorder | `sudo apt install build-essential cmake` |
| Boost | 1.65+ (`program_options`) | recorder command line | `sudo apt install libboost-program-options-dev` |
| systemd | (OS) | autostart and restart | in the OS |

## Set up

```bash
cd HERON_DEPLOY_SOFTWARE
uv sync --all-packages
uv run pytest            # 89 tests, about 5 s (one headless TUI test)
uv run ruff check .      # lint
uv run mypy Common_Software/src Onboard_Software/src Base_Software/src
```

## Run

Onboard (payload):

```bash
uv run heron-onboard check-config --config Onboard_Software/config/onboard.toml
uv run heron-onboard run --config Onboard_Software/config/onboard.toml          # what systemd runs
uv run heron-onboard record --config Onboard_Software/config/onboard.toml --seconds 60   # bench capture
```

Base (ground laptop):

```bash
uv run heron-base check-config --config Base_Software/config/base.toml
uv run heron-base tui --config Base_Software/config/base.toml       # live display; keys: s start, x stop, t status, p ping, q quit
uv run heron-base send start --config Base_Software/config/base.toml --flight-id lake_a_run1
uv run heron-base send stop  --config Base_Software/config/base.toml
uv run heron-base monitor --config Base_Software/config/base.toml   # one telemetry line per second
uv run heron-base demo                                              # display against a fake payload, no hardware
```

Bench link test on one machine, no SDRs (two terminals):

```bash
uv run heron-onboard run --config Onboard_Software/config/onboard.bench-loopback.toml --fake-capture
uv run heron-base tui --config Base_Software/config/base.bench-udp.toml
```

## Layout

```
pyproject.toml            workspace root: members, dev tools, pytest/ruff/mypy settings
uv.lock                   exact versions for every machine (commit it)
Common_Software/          heron_common: config.py, protocol/ (messages, framing, transports)
Onboard_Software/         heron_onboard: supervisor, state_machine, capture/, disk_monitor, health
  config/                 onboard.example.toml (template), onboard.bench-loopback.toml
  recorder/               heron_recorder.cpp, CMakeLists.txt, README.md
  systemd/                heron-onboard.service
Base_Software/            heron_base: link_client, tui/, gnss/, flight_log
  config/                 base.example.toml, base.bench-udp.toml
*/tests/                  pytest suites (see docs/TESTING.md)
```

Copy `*.example.toml` to `onboard.toml` / `base.toml` and edit; the
machine configs are git-ignored because they hold serial numbers and
ports.

## Status

- Python packages: complete for the first bench; 89 unit, loop, and
  headless-TUI tests pass; ruff and mypy clean. The onboard supervisor
  (fake capture) and the base CLI were run end to end over UDP on a
  Windows laptop on 2026-09-17.
- C++ recorder: written against the UHD 4.x API, **not yet compiled or
  run on hardware**. Build it on the NUC (`recorder/README.md`) and run
  `docs/TEST_BENCH_ROUTINE.md` section 3.
- MAVLink transport: written with pymavlink, **not yet run against a
  Cube**. Confirm TUNNEL routing (Q-014) on the bench.
