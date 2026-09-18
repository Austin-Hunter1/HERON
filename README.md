# HERON

## Cloning

This repository uses git submodules. To pull everything, including submodule contents, clone with:

```bash
git clone --recurse-submodules https://github.com/Austin-Hunter1/HERON.git
```

If you already cloned without that flag, run:

```bash
git submodule update --init
```

## Submodules

- [gps-tracking-example](gps-tracking-example) — linked from [cu-sense-lab/gps-tracking-example](https://github.com/cu-sense-lab/gps-tracking-example) (upstream reference; HERON's working copy is `gnss_processing/`)
- [gnss_processing/submodules/gnss-tools](gnss_processing/submodules/gnss-tools) — linked from [cu-sense-lab/gnss-tools](https://github.com/cu-sense-lab/gnss-tools), used by `gnss_processing/`
- [HERON_WRITING](HERON_WRITING) — reports and papers, linked from [Austin-Hunter1/HERON_WRITING](https://github.com/Austin-Hunter1/HERON_WRITING)

## Where things are

- `HERON_DEPLOY_SOFTWARE/` — the flight computer and ground station software (see its `README.md`).
- `gnss_processing/` — the post-processing tools.
- `docs/` — project documents; start with `docs/GETTING_STARTED.md`.
- `SURGE/` — the 2022 predecessor project, reference only.

## GNSS Processing

[gnss_processing](gnss_processing) is HERON's own working copy of `gps-tracking-example`
(notebooks and code edits for the UAS GNSS-R tracking pipeline — reacquisition after
data-drop overflows, navigation-message decoding across a stitched run, and related
fixes) kept as a plain folder here rather than a submodule, since this project does not
have push access to `cu-sense-lab/gps-tracking-example`. See its own
[README](gnss_processing/README.md) for what differs from upstream.
