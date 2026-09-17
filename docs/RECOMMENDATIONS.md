# RECOMMENDATIONS — Platform and Tooling Choices

Status: Draft 2, 2026-09-17. These are recommendations with reasons.
The team confirms or changes them in `DECISIONS.md`. Sections 1, 3,
and 5 have been overtaken by decisions the team has since made
(D-018, D-020, D-021); this draft updates them to match. Section 4
(containers) is still open (Q-007).

## 1. Operating system: Ubuntu Server LTS, headless

Recommendation: **Ubuntu Server 24.04 LTS (or 22.04 LTS), minimal
install, headless**, on the payload computer.

Reasons: Ettus supports UHD best on Ubuntu LTS; the legacy SURGE stack
ran on Linux; LTS gives 5 years of stability; the apt UHD 4.x package
supports both the B210 and the B200mini (D-020), so the caution about
building UHD 3.15 from source for a B100 no longer applies; Server (no
desktop) leaves more RAM and CPU for capture on an 8 GB machine and
removes GUI processes that can cause USB or scheduling jitter.

### Headless: pros and cons

Pros:

- Lower CPU, RAM, and power use; fewer processes that can steal USB
  bandwidth during capture.
- Fewer packages: faster boot, fewer updates, smaller attack surface.
- Forces all operations through SSH and scripts, so bench behavior
  and field behavior are identical and repeatable.
- No monitor/keyboard on the drone anyway; a GUI only helps on the
  bench.

Cons:

- Field debugging needs a working network path (Wi-Fi AP or travel
  router) and an SSH-capable laptop; if the network fails, you are
  blind (mitigate: fallback direct Ethernet cable in the field kit,
  and a serial console cable as last resort).
- Steeper learning curve for team members who are new to the Linux
  command line.
- No graphical quick-look plots on the payload itself (mitigate:
  quick-look tools run on the field laptop against offloaded or
  SSH-piped data).

Middle path if the team wants a GUI on the bench: install Server, and
add a desktop only on a separate bench boot drive. Do not fly a
desktop install.

## 2. Language: keep Python, keep the C++ capture path

Recommendation: **Python 3.11+** for everything except the sample
stream; **C++ against the UHD API** for capture.

Update, 2026-09-17 (D-018): the plan was to adapt the legacy recorder
(`SURGE/NUC_scripts/SDR_backup_files/rx_multi_to_file.cpp`). That file
depends on four CSU "UHD extensions" headers
(`buffered_fstream.hpp`, `cbuff_handler.hpp`, `io_runner.hpp`,
`mu_meta.hpp`) that are not in this repository, so it cannot build. The
team wrote a new recorder (`Onboard_Software/recorder/heron_recorder.cpp`)
against the plain UHD 4.x API instead, keeping the legacy structure
(per-unit process, timed start, fixed-length segments). The reasons
below for staying in C++ still hold.

Reasons:

- The performance-critical work is moving bytes from USB to SSD. UHD
  is C++ at the core, so the recorder talks to it directly. Python
  only supervises (start/stop, the ground link, disk checks), which
  needs trivial CPU.
- A full rewrite in C++ or Rust would not let you buy a smaller
  computer: the size driver is USB 3.0 bandwidth, SSD write speed,
  and RAM buffers, not interpreter speed.
- Six engineers with mixed backgrounds can read and change Python
  quickly; that serves the "decisions change rapidly" requirement.
- The post-processing ecosystem (numpy/scipy, legacy SURGE scripts)
  is Python.

If the team later wants one language for all of it, **Rust** is the
best candidate (uhd-rust bindings, memory safety), with `cargo` as
the uv-equivalent (built-in package manager + lock file). For C++,
the closest equivalents are `vcpkg` or `conan` plus CMake. But this is
not recommended now: it costs weeks and buys little.

## 3. Python packaging: uv

Use `uv` (see the explainer in `GETTING_STARTED.md`). Built as one
`uv` workspace at `HERON_DEPLOY_SOFTWARE/`, with a root
`pyproject.toml` + `uv.lock` shared by three member packages
(`Common_Software`, `Onboard_Software`, `Base_Software`) so the flight
computer and the ground laptop each install exactly what CI tested,
from one lock file. `Common_Software` holds the config loader and the
payload-link protocol that both sides need; the other two hold
nothing the other does not need.

## 4. Containers: Podman, and only off the critical path

Recommendation: **Podman** over Docker, and containers for ground
tools and CI only — **bare metal (systemd) for onboard capture**.

Podman vs Docker:

- Podman is daemonless and runs rootless; a container cannot ride a
  root daemon, which matters on a shared lab machine.
- Podman is drop-in CLI-compatible with Docker (`alias docker=podman`)
  and builds standard OCI images, so nothing is lost.
- Docker Desktop has licensing terms for organizations; Podman is
  fully open. On Linux servers both are free, but Podman ships in the
  Ubuntu/Fedora repos cleanly.
- Podman generates systemd units (Quadlet) natively, which fits our
  autostart design.

Why not containerize the onboard capture: USB passthrough of four
SDRs, udev rules, real-time scheduling, and raw disk throughput all
get harder inside a container, and add failure modes you must debug
headless in a field. The payload gains nothing: the whole machine is
already dedicated. Use containers where portability pays:
a `Base_Software`/post-processing image any engineer can run on any
laptop, and the CI image that pins UHD for regression tests.

## 5. Smaller/cheaper computer path (Q-005)

To shrink hardware later, the bottlenecks to verify are: at least two
independent USB 3.0 controllers, sustained SSD writes at the
configured rate with 2× margin, and UHD 4.x support. Candidates to
bench (not endorsements): x86 mini-PCs (N100 class) and ARM boards
with real USB 3.0 plus SATA/NVMe. Remember the four-SDR USB topology:
all four units (2× B210, 2× B200mini, D-020) are USB 3.0, so a
replacement needs four USB 3.0 ports spread across at least two
controllers. Test with the endurance capture test in `TESTING.md`
before any swap. The 8 GB NUC stays the baseline until a candidate
passes.
