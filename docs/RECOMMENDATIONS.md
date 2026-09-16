# RECOMMENDATIONS — Platform and Tooling Choices

Status: Draft 1, 2026-09-16. These are recommendations with reasons.
The team confirms or changes them in `DECISIONS.md`.

## 1. Operating system: Ubuntu Server LTS, headless

Recommendation: **Ubuntu Server 24.04 LTS (or 22.04 LTS), minimal
install, headless**, on the payload computer.

Reasons: Ettus supports UHD best on Ubuntu LTS; the legacy SURGE stack
ran on Linux; LTS gives 5 years of stability (but see Q-011 — the
B100s likely need UHD 3.15 built from source, not the apt 4.x);
Server (no desktop) leaves more RAM and CPU for capture on an 8 GB
machine and removes GUI processes that can cause USB or scheduling
jitter.

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
stream; **keep and adapt the legacy C++ UHD recorder**
(`rx_multi_to_file`) for capture.

Reasons:

- The performance-critical work is moving bytes from USB to SSD. The
  legacy C++/UHD recorder already does this well; UHD is C++ at the
  core. Python only supervises (start/stop, the ground link, disk checks),
  which needs trivial CPU.
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

Use `uv` (see the explainer in `GETTING_STARTED.md`). One
`pyproject.toml` + `uv.lock` per software package
(`Onboard_Software`, `Base_Software`) so the flight computer installs
exactly what CI tested.

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

Why not containerize the onboard capture: USB passthrough of two
B210s, udev rules, real-time scheduling, and raw disk throughput all
get harder inside a container, and add failure modes you must debug
headless in a field. The payload gains nothing: the whole machine is
already dedicated. Use containers where portability pays:
a `Base_Software`/post-processing image any engineer can run on any
laptop, and the CI image that pins UHD for regression tests.

## 5. Smaller/cheaper computer path (Q-005)

To shrink hardware later, the bottlenecks to verify are: two
independent USB 3.0 controllers, sustained SSD writes at the
configured rate with 2× margin, and UHD support. Candidates to bench
(not endorsements): x86 mini-PCs (N100 class) and ARM boards with
real USB 3.0 plus SATA/NVMe. Remember the four-SDR USB topology: two
USB 3.0 controllers for the B210s plus two USB 2.0 ports for the
B100s. Test with the endurance capture test in
`TESTING.md` before any swap. The 8 GB NUC stays the baseline until a
candidate passes.
