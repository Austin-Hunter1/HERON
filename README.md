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

- [gps-tracking-example](gps-tracking-example) — linked from [cu-sense-lab/gps-tracking-example](https://github.com/cu-sense-lab/gps-tracking-example)

## GNSS Processing

[gnss_processing](gnss_processing) is HERON's own working copy of `gps-tracking-example`
(notebooks and code edits for the UAS GNSS-R tracking pipeline — reacquisition after
data-drop overflows, navigation-message decoding across a stitched run, and related
fixes) kept as a plain folder here rather than a submodule, since this project does not
have push access to `cu-sense-lab/gps-tracking-example`. See its own
[README](gnss_processing/README.md) for what differs from upstream.
