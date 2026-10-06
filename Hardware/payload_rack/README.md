# HERON payload rack, Rev A (concept)

A blade-style rack for 2× B210, 2× B200, the bare NUC7i3DNB board, a hot-swap 2.5" SATA SSD, and the clock board. It is made from laser-cut sheet plus printed ASA and TPU, with no metal structure and no fan.

**Envelope:** 172 W × 209 D × 126 H mm (front = RF side). Rack mass without boards is about 0.46 kg in 3 mm plywood plus printed parts. That excludes screws, inserts and standoffs (about 40 g) and the copper tape (about 25 g).

## How it works
- **Blades.** Each module screws to a 3 mm laser-cut tray through its own mounting holes. A TPU C-rail clips onto the tray's top edge and another onto its bottom edge.
  - The four SDR trays are solid. Each carries a grounded copper-tape shield (see below). The NUC tray is skeletal.
  - The rails slide in printed ASA rail strips bolted to the top and bottom plates.
  - Any blade pulls out the front on its own: back off the thumbscrews (SDR blades), swing the two latches aside, and pull the finger tab.
  - The SDR blades have an axial datum and a preload. See "SMP blind-mate" below.
- **Vertical blades, not horizontal shelves.** The boards are solid PCBs. Stacked horizontally, they would block the drone's downwash.
  - Standing vertically, they leave 7 mm air channels between blades that run top to bottom (downwash) and front to back (forward flight).
  - The top and bottom plates are slotted over every channel. The rear is open apart from the clock strip.
- **The SDRs are rotated so the RF SMAs face forward.** Each B2x0 has its RF SMAs on one short edge, and USB-B, power and three SMAs on the other. Two of those three are the 10 MHz and PPS inputs.
- **NUC.** The NUC is front-aligned on a short blade (107 mm). Its rear I/O edge faces the SSD bay, with a 29 mm gap for cables.
- **SSD bay (behind the NUC, in the NUC slot).** There is no SSD blade now, so the rack is 18 mm narrower.
  - The drive stands on its connector edge. A printed sled screws to the drive's bottom-face holes.
  - Drive and sled drop through a hatch in the top plate into a printed cage (`ssd_cage`). The cage has two U-channel guides and a base that holds the 22-pin receptacle adapter.
  - The cable leaves the base toward the NUC. A swing latch on the top plate holds the sled handle down.
  - To swap: open the latch and pull the cord loop on the handle. The removal path is checked clear of all parts.
  - Swap with the payload powered off.
- **Clock board rev B.** A 118 × 44 mm strip on the rear plate, behind the four SDRs (`../clock_board_revB/`).
  - Its 8 SDR jacks (J3–J10) are Amphenol RF SMP-MSLD-PCT SMP male jacks. They face forward, coaxial with each SDR's rear reference SMAs. J2 (PPS IN) stays an SMA jack.
  - There are no cable jumpers. Each SDR blade blind-mates to the board. See "SMP blind-mate" below.

## SMP blind-mate (SDR to clock board)
**Parts in the RF path (for each of the 8 jacks):**
- An SMA-male to SMP-male smooth-bore adapter. Screw it on the SDR rear 10 MHz or PPS SMA jack. Candidates: Fairview SM8810, Pasternack PE91311.
- An SMP female-female bullet. It sits in the board jack. The adapter mates with the bullet when the tray slides in.
- The board jack: Amphenol RF SMP-MSLD-PCT (SMP male, limited detent, vertical through-hole).

**Stack-up along Y.** One chain of named parameters at the top of `heron_rack.py` sets `CLK_Y0`. `REAR_Y0`, the depth `D`, the walls, the plates and the windows follow it.
`CLK_Y0 = SDR_SMA_TIP_Y + ADAPTER_REACH + BULLET_GAP + SMP_JACK_H`

| Parameter | Value (mm) | Status |
|---|---|---|
| `ADAPTER_REACH` (SDR SMA tip to adapter SMP mating face) | 16.0 | VERIFY: measure on a real B210 with the adapter on |
| `ADAPTER_D` (adapter body diameter) | 8.0 | VERIFY (SM8810 is 7.87) |
| `SMP_JACK_H` (board front face to jack mating face) | 4.09 | From the Amphenol IGES model |
| `BULLET_L` (bullet length) | 9.90 | PROPOSED (Amphenol SMP-FSBA-990, Rosenberger 9.90) |
| `BULLET_D` (bullet body diameter) | 3.43 | |
| `BULLET_GAP` (gap between the two male mating faces) | `BULLET_L - 5.6` = 4.30 | VERIFY: from Rosenberger data (6.45 mm bullet gives 0.85 mm gap). Ask Amphenol for its table. |

**Radial position.** The jacks are soldered in place with the SDRs mated, so each slot sets its own radial position. The rack must keep that position repeatable:
- `CHAN_CLR` is the total clearance between the strip channel and the TPU rail: 0.2 mm (PROPOSED, was 0.4).
- `RAIL_BASE_GAP` is the Z gap between the TPU rail and the strip base: 0.1 mm (PROPOSED, was 0.2).
- The NUC blade uses the same strip code. It gets the same values.
- `TRAY_OFF` stays 5.7 mm, so the SDR axes stay on the jack axes.

**Axial position (datum and preload).** The blind-mate does not align itself along Y. The rack sets it (PROPOSED):
- **Rear stop.** The rear stop block of each SDR rail strip (top and bottom) holds an M3 heat-set insert and an M3 × 12 mm set screw on the Y axis. The tip of the screw is the datum of the tray rear end. The nominal tray rear end is `TRAY_Y1`. `STOP_ADJ` (1.5 mm) is the adjust range, +/-. Put the insert in from the front face of the block, and the screw in from the rear.
- **Front preload.** Each SDR latch has a boss with an M3 heat-set insert and an M3 thumbscrew along +Y. The tip pushes on the tray front edge. The tray is clamped between the thumbscrew and the rear stop screw.
- A screw gives the force, not TPU. The mating force is up to 9 N for each SMP smooth-bore interface (18 N for each SDR).
- To remove a tray: back off the thumbscrew, swing the latch aside, and pull the finger tab.
- The latch is a lever. The thumbscrew pushes the latch forward and the pivot screw holds it. Check the latch for creep or cracks after a test (VERIFY).
- **Rear stop access.** The rear plate has one access hole (`STOP_ACCESS_D`, 4.0 mm) on the axis of each rear stop screw: 8 holes, at Z 5.5 mm (under the clock board) and Z 114.5 mm (above it). Use a long 1.5 mm hex key from behind the rack. You can adjust each tray with the clock board and all SDRs installed.
- `check_fit.py` proves a clear path for a 3 mm tool shaft (`STOP_KEY_D`) from the rear plate to each stop block. It also checks that at least 2 mm of plate material stays around each hole.
- The rear plate screw moved from W/2 to midway between the 2nd and 3rd SDR trays (X 94.7 mm). At W/2, its T-slot nut pocket cut into the slot 2 access hole.

**Solder in place (procedure).**
1. Put the 8 SMP jacks loose in the clock board holes (no solder). Put a bullet in each jack. Fit the board on the rear plate.
2. Screw an adapter on the 10 MHz and PPS SMA jack of each SDR. Install all four SDR blades.
3. Set the rear stop screws through the rear-plate access holes. Tighten the four thumbscrew pairs so each tray is clamped on its stop.
4. The jacks now sit on the adapter axes. Solder them from the back of the board, through the windows in the rear plate.
5. Back off the thumbscrews. Remove the SDR blades and the board. Finish the joints and clean the board.

**Rear plate soldering windows.** There is one window behind each SDR jack column (`SOLDER_WIN_DX` = 7 mm each side of the jack axis in X, `SOLDER_WIN_DZ` = 6 mm above the upper jack and below the lower jack). Each window has a keep-out disc around the board mounting holes (`CLK_HOLE_KEEP` = 4 mm of material). The laser file `rear_plate.dxf` shows the windows.

**SSD bay.** The shorter stack-up moves the rear plate forward by 9.6 mm. The SSD bay no longer fits at 136 mm. `SSD_Y0` is now the smaller of 136 mm and the value that leaves `SSD_REAR_GAP` (1 mm, PROPOSED) to the rear plate. It is 127.7 mm now. The cable gap behind the NUC gets smaller. The team accepted this on 2026-10-05.

**Check:** `check_fit.py` also prints a mate report. For each SDR and each jack it shows the radial offset between the adapter axis and the jack axis (limit 0.05 mm) and the gap (must equal `BULLET_GAP` +/- 0.01 mm). The mated adapter, bullet and jack touch by design, so the clash test skips those pairs.

## SDR shielding (copper tape on the solid trays)
- Put copper tape with **conductive adhesive** on the **board side** of each SDR tray. Cover the full face.
- Bond the tape to the SDR ground through the board's grounded mounting holes:
  - Use **metal** M3 × 6 mm F-F standoffs (aluminium is lightest) on the SDR trays, not nylon.
  - Use metal M3 screws from the back of the tray. The standoff clamps the tape.
- Put one layer of Kapton over the tape, except at the four standoff pads. The board underside has
  through-hole pins and SMA bodies 4–5 mm from the tape.
- Result: each SDR sits between its own grounded tray and the next tray. The B210_1 tray also separates
  the SDRs from the NUC.
- Limits:
  - The top, bottom, front and rear edges of each bay stay open. At L-band, the wavelength is about
    200 mm, so these openings leak.
  - Expect a useful reduction of board-to-board coupling, not a sealed shield.
  - Coupling through the antenna cables, USB cables and the shared power is often larger. Use shielded
    USB cables with ferrites, and keep the RF cables apart.

## Files (`out/`)
| File | What |
|---|---|
| `HERON_rack_revA_assembly.step` | Full assembly with the real Ettus B210/B200 models (82 MB). Open it in Fusion (File → Open / Upload). Not in git: run `heron_rack.py` to make it. Put the Ettus STEP files in `ref/` first. |
| `HERON_rack_revA_assembly_noboards.step` | Rack parts only, without boards (7 MB). Not in git: `python3 heron_rack.py --norefs` makes it. |
| `laser_dxf/*.dxf` | Cut files, 1:1 mm, closed outlines. The suffix is the quantity. |
| `print_stl/*.stl`, `step_parts/*.step` | Printed parts plus a STEP of every unique part. |
| `mass.csv` | Per-part volume and mass for plywood, acrylic or Delrin sheet. |
| `section_y80.png`, `section_y170.png`, `preview_*.png` | Sections through the blades (y 80) and the SSD bay (y 170), and views. |
| `../heron_rack.py` | Parametric generator. Edit the numbers at the top and run `python3 heron_rack.py` (CadQuery). `check_fit.py` reruns the interference check. |

## Cut and print list
**Laser (3 mm):** bottom plate ×1, top plate ×1 (with the SSD hatch), side wall ×2, rear plate ×1, SDR tray (solid) ×4, NUC tray ×1.

**Print, ASA-CF (about 40% infill, 4 walls):**
- SDR rail strip ×8: 4 as-is for the bottom, 4 **mirrored** in the slicer for the top
- NUC rail strip ×2: 1 as-is, 1 mirrored
- SDR latch with preload boss ×8 (`latch_sdr_preload`)
- latch ×3 (2 for the NUC blade, 1 for the SSD hatch)
- SSD cage ×1, SSD sled ×1

**Print, TPU 95A (100%):** SDR C-rail ×8, NUC C-rail ×2.

**Hardware:**
- M3 heat-set inserts (about 56: about 40 for the rack, plus 8 in the rear stops and 8 in the SDR latches)
- M3 screws plus square nuts for the T-slot joints (16)
- M3 × 6 mm F-F standoffs: 16 **metal** for the SDRs (they ground the copper tape), plus nylon for the NUC
- M3 flat-head (countersunk) screws ×4 for the SSD sled
- copper tape with conductive adhesive (about 0.2 m² for 4 trays) and Kapton tape
- a short cord or ribbon pull loop for the SSD sled handle
- M3 × 8 mm standoffs ×4 for the clock board
- SMP blind-mate parts, in total for the four SDRs:
  - 8 SMA-male to SMP-male smooth-bore adapters (2 for each SDR), for example Fairview SM8810 or Pasternack PE91311
  - 8 SMP female-female bullets (2 for each SDR), for example Amphenol SMP-FSBA-990
  - 8 Amphenol RF SMP-MSLD-PCT jacks (board part J3–J10)
  - 8 M3 × 12 mm hex-socket set screws (2 for each SDR, rear stops) and their inserts
  - 8 M3 thumbscrews, about 16 mm long (2 for each SDR, one in each latch) and their inserts
- one 22-pin SATA receptacle adapter: a small PCB with a vertical 22-pin receptacle and a sideways cable exit (fits a 45 × 12 × 10 mm pocket)

## Materials
- **Acrylic is fine for fit-check prototypes but not for flight.** It cracks at screw holes and tab roots under shock. For flight, cut the same DXFs from 3 mm birch aircraft plywood (lightest, tough, RF-transparent) or Delrin (heavier but very tough). `mass.csv` shows the difference: about 341 g plywood, 597 g acrylic, 708 g Delrin for the sheet parts.
- **CF-ASA** is right for the rail strips: they're stiff, and they also stiffen the plates. Aero-ASA saves little on such small parts.
- **The copper tape shields the SDRs from each other, not the antennas from the box.** The NUC and four USB 3.0 links radiate broadband noise, and USB 3.0 noise is a known GNSS desense source. Keep the antennas well away from this box and use ferrite-clamped, shielded USB cables.

## Cooling plan
- **In flight:** downwash and forward-flight air pass through the blade channels. A B210 draws only about 3–4 W at 2×2. The NUC keeps its own cooler.
- **This needs the blades to stand vertically on the drone** (rack Z axis up). If the rack flies as a stack with the blades horizontal, the solid SDR trays block the downwash. Only forward-flight air then flows front to back through the channels.
- **On the ground with props stopped there is no airflow.** Keep powered-on ground time short, or hold a handheld fan at the slots during preflight. Watch the temperatures in telemetry.
- **Mount the box where downwash actually reaches it.** Not dead-centre under a solid fuselage.

## VERIFY before cutting
1. **NUC7i3DNB mounting holes and cooler height** (`NUC_HOLES`, `NUC_TOP`). These are placeholders because Intel's spec doesn't give the numbers in text. Measure your board; the NUC tray holes come from these values.
1a. **SMP stack-up.** Measure `ADAPTER_REACH` and `ADAPTER_D` on a real B210 with the adapter on. Get the real face-to-face gap for your bullet and jack (`BULLET_GAP`) from Amphenol. Change the values and rerun. Everything downstream moves.
1b. **PROPOSED, not confirmed:** `BULLET_L`, `STOP_ADJ`, `CHAN_CLR`, `RAIL_BASE_GAP`, the rear stop block (`STOP_BLOCK_H`, `STOP_WALL`), the preload boss (`PRE_BOSS_D`, `PRE_BOSS_T`), the window sizes (`SOLDER_WIN_DX`, `SOLDER_WIN_DZ`, `CLK_HOLE_KEEP`) and `STOP_ACCESS_D`. `SSD_REAR_GAP` is confirmed (2026-10-05: the 8 mm smaller NUC cable gap is acceptable). Test the radial and axial repeatability on a prototype first.
2. **Which rear SMAs are 10 MHz and PPS.** The model has three rear SMAs at 14.2, 34.2 and 70.9 mm along the board edge. I've assumed the adjacent pair (14.2 / 34.2) are 10 MHz / PPS; check the silkscreen. If it's a different pair, change `SDR_REAR_SMA` and rerun; the clock strip moves with it.
3. **SSD:**
   - Drive thickness (`SSD_T`, 7 or 9.5 mm).
   - The bottom-hole positions. The sled has slots that cover both common readings.
   - The connector offset `SSD_CONN_C`, and the pocket size for the receptacle adapter you buy.
4. **NUC rear I/O edge.** The model assumes that the edge faces the SSD bay. Check which board edge has the USB 3.0 ports that the SDR cables use.
5. **Drone mount pattern.** Not added yet. Do not use the SSD hatch area of the top plate.
6. **Laser kerf.** Tabs and slots assume 0.15 mm per side. Cut one corner joint first.

## Clock board rev B
The board is in `../clock_board_revB/` (118 × 44 mm, KiCad 7). `heron_rack.py` reads the jack
positions and footprint names from that KiCad file. J3–J10 are SMP jacks (SMP-MSLD-PCT). J2 is an SMA jack. If the file has an old 132134 footprint on J3–J10, the script still draws them as SMP.
All 8 SDR jacks are coaxial with the SDR reference SMAs (error 0.000 mm).
The board mounts on the rear plate with four M3 × 8 mm standoffs. Its holes are at world (X, Z) =
(50, 50), (160, 50), (50, 14) and (160, 14).
