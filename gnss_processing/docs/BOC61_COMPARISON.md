# How to compare tracking with and without BOC(6,1)

This document tells you how to run the same recording with two models of the
pilot and compare the results. It applies to BeiDou B1C and GPS L1C.
The design is in `docs/DECISIONS.md` (D-077, D-078).

## What the switch does

| Signal | Default model | `BOC61_ENABLED = True` | `BOC61_ENABLED = False` |
| --- | --- | --- | --- |
| B1C | BOC(1,1) pilot only (narrow band, D-062) | adds a third component `B1CP61`, the BOC(6,1) part (1/11 of the power) | same as the default |
| L1C | full TMBOC pilot (BOC(6,1) on 4 of every 33 chips) | same as the default | BOC(1,1) on every pilot chip |

The default of each signal does not change. Default file names do not change.
A comparison run adds `_wb` (wide band) or `_nb` (narrow band) to the file name,
for example `RX7_BDS_B1C_0-120s_wb.h5`.

## Steps

1. Set the sample rate. The BOC(6,1) lobes need more than 14 Msps. Use 25 Msps
   or more. Notebook 01 prints a warning when the rate is too low.
2. Run notebook 01 with the default (`BOC61_ENABLED = None`). Keep the file.
3. Run notebook 01 again on the same recording, with the same start offset and
   duration. Set `BOC61_ENABLED` to the other value (B1C: `True`; L1C: `False`).
4. Compare the two files:

       python scripts/compare_boc61.py DEFAULT.h5 DEFAULT_wb.h5

5. For a position fix from the variant file, set `TRACKING_VARIANT = "wb"` (or
   `"nb"`) in notebook 02.

## What the numbers mean

- **C/N0 change.** Mean C/N0 of the pilot. Wide band should be higher. The most you can
  gain is about 0.56 dB on the pilot (10 log10 33/29). Synthetic data at 16 Msps gave 0.33 dB.
- **CMC ratio.** Scatter of code minus carrier after a local fit, variant over
  default. A value below 1 means the variant has the lower noise and multipath.
  Synthetic data gave 0.90.
- **Lock losses.** Number of times the PLL fell back to the FLL.

Check the first and last minute of the run by eye. A C/N0 gain that comes with
more lock losses is not a gain.

## Limits you must know

- Wide-band B1C has two subcarrier rates. The double estimator (the BOC
  discriminator that the B1C default uses, D-071) works with one rate only.
  The channel refuses it. So notebook 01 runs wide-band B1C with one delay loop and
  `EPL_chip_spacing = 0.2`. The narrow-band run keeps the double estimator.
  So the two runs differ in the model and also in the discriminator. Q-020 asks which
  comparison you want.
- Narrow-band L1C uses the B1C subcarrier taps (0.1 chip), because its pilot is BOC(1,1).
- The 0.2 chip value is from synthetic data only. Real data has not tried it.
- `CMC` uses the subcarrier loop offset when the channel has one. Use
  `--no-subcarrier-correction` to see the code loop alone.
