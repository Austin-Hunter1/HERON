# HERON payload rack, Rev A (concept)

A blade-style rack for 2× B210, 2× B200, the bare NUC7i3DNB board, a hot-swap 2.5" SATA SSD, and the clock board. It is made from laser-cut sheet plus printed ASA and TPU, with no metal structure and no fan.

**Envelope:** 172 W × 210 D × 126 H mm (front = RF side). Rack mass without boards is about 0.46 kg in 3 mm plywood plus printed parts. That excludes screws, inserts and standoffs (about 40 g) and the copper tape (about 25 g).

## How it works
- **Blades.** Each module screws to a 3 mm laser-cut tray through its own mounting holes. A TPU C-rail clips onto the tray's top edge and another onto its bottom edge.
  - The four SDR trays are solid. Each carries a grounded copper-tape shield (see below). The NUC tray is skeletal.
  - The rails slide in printed ASA rail strips bolted to the top and bottom plates.
  - Any blade pulls out the front on its own: back off the thumbscrews (SDR blades), swing the two latches aside, and pull the finger tab.
  - The SDR blades plug directly onto the clock board. The mate is the axial stop. See "SMP direct mate" below.
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
- **Clock board rev C.** A 118 × 44 mm strip on the rear plate, behind the four SDRs (`../clock_board_revC/`).
  - Its 8 SDR jacks (J3–J10) are Amphenol RF SMP-MSSB-PCT10T SMP male jacks (smooth bore). They face forward, coaxial with each SDR's rear reference SMAs. J2 (PPS IN) stays an SMA jack.
  - There are no cable jumpers. Each SDR blade plugs directly onto the board. See "SMP direct mate" below.

## SMP direct mate (SDR to clock board)
**Parts in the RF path (for each of the 8 jacks):**
- One Cinch/Johnson 134-1019-451 adapter: SMA plug to SMP female (jack, female socket). Screw it on the SDR rear 10 MHz or PPS SMA jack. It plugs directly onto the board jack when the tray slides in. There is no bullet.
  - Drawing data: overall length 14.25 mm (0.561 +/- 0.020 in). Hex 5.54 mm across flats (0.218 in REF).
  - SMP engage force: 15 N maximum (3.4 lbf). Disengage force: about 22 N typical (5 lbf).
- The board jack: Amphenol RF SMP-MSSB-PCT10T (SMP male, smooth bore, vertical through-hole). Square body 5.99 x 5.99 mm. The mating face is 4.09 mm from the board front face.

**Stack-up along Y.** One chain of named parameters at the top of `heron_rack.py` sets `CLK_Y0`. `REAR_Y0 = CLK_Y0 + 1.6 + CLK_STANDOFF` (20 mm standoffs). The depth `D`, the walls, the plates and the windows follow it.
`CLK_Y0 = SDR_SMA_TIP_Y + ADAPTER_REACH + SMP_JACK_H`

| Parameter | Value (mm) | Status |
|---|---|---|
| `ADAPTER_REACH` (SDR SMA tip plane to board jack mating face, adapter fully mated) | 9.0 | VERIFY: placeholder. Estimate from the 14.25 mm overall length minus the SMA thread overlap and the SMP insertion depth. Measure it on a real B210 with the adapter fitted and mated on a jack. |
| `ADAPTER_D` (adapter model diameter) | 6.40 | Hex across corners: 5.54 / cos(30 deg). Source: Cinch drawing. |
| `SMP_JACK_H` (board front face to jack mating face) | 4.09 | From the Amphenol drawing |
| `STOP_MATE_MARGIN` (stop screw tip behind the fully mated tray end) | 0.3 | PROPOSED |

**Radial position.** The jacks are soldered in place with the SDRs mated, so each slot sets its own radial position. The rack must keep that position repeatable:
- `CHAN_CLR` is the total clearance between the strip channel and the TPU rail: 0.2 mm (PROPOSED, was 0.4).
- `RAIL_BASE_GAP` is the Z gap between the TPU rail and the strip base: 0.1 mm (PROPOSED, was 0.2).
- The NUC blade uses the same strip code. It gets the same values.
- `TRAY_OFF` stays 5.7 mm, so the SDR axes stay on the jack axes.

**Axial position (the mate is the stop).** There is no bullet, so there is no axial float. The mated connectors set the tray position (PROPOSED):
- **Front thumbscrew.** Each SDR latch has a boss with an M3 heat-set insert and an M3 thumbscrew along +Y. The tip pushes on the tray front edge. The thumbscrew seats both connectors. `TRAY_Y1` in the model is the fully mated tray rear end.
- **Rear stop (backup).** The rear stop block of each SDR rail strip (top and bottom) holds an M3 heat-set insert and an M3 × 12 mm set screw on the Y axis. The stop does not set the mate. It keeps the thumbscrew from overloading the jacks. Put the insert in from the front face of the block, and the screw in from the rear.
- **How the margin is applied.** The nominal screw tip is at `STOP_TIP_Y = TRAY_Y1 + STOP_MATE_MARGIN`. Set each stop screw so the tray stops `STOP_MATE_MARGIN` (0.3 mm) behind the fully mated position. `STOP_ADJ` (1.5 mm) is the adjust range, +/-, about this nominal tip.
- A screw gives the force, not TPU. The engage force is up to 15 N for each SMP interface (30 N for each SDR). The disengage force is about 22 N typical for each interface.
- To remove a tray: back off the thumbscrew, swing the latch aside, and pull the finger tab.
- The latch is a lever. The thumbscrew pushes the latch forward and the pivot screw holds it. Check the latch for creep or cracks after a test (VERIFY).
- **Rear stop access.** The rear plate has one access hole (`STOP_ACCESS_D`, 4.0 mm) on the axis of each rear stop screw: 8 holes, at Z 5.5 mm (under the clock board) and Z 114.5 mm (above it). Use a long 1.5 mm hex key from behind the rack. You can adjust each tray with the clock board and all SDRs installed.
- `check_fit.py` proves a clear path for a 3 mm tool shaft (`STOP_KEY_D`) from the rear plate to each stop block. It also checks that at least 2 mm of plate material stays around each hole.
- The rear plate screw moved from W/2 to midway between the 2nd and 3rd SDR trays (X 94.7 mm). At W/2, its T-slot nut pocket cut into the slot 2 access hole.

**Solder in place (procedure).** The jacks are aligned by mating them on the adapters with the SDRs installed. Then you solder them from the back through the windows.
1. Put the 8 SMP jacks loose in the clock board holes (no solder). Fit the board on the rear plate.
2. Screw a 134-1019-451 adapter on the 10 MHz and PPS SMA jack of each SDR. Install all four SDR blades. Push each tray in so each adapter mates on its jack.
3. Tighten the thumbscrews until each tray is fully mated. The thumbscrew seats both connectors.
4. Set each rear stop screw through the rear-plate access hole so the tray stops `STOP_MATE_MARGIN` (0.3 mm) behind the fully mated position. The stop is a backup.
5. The jacks now sit on the adapter axes. Solder them from the back of the board, through the windows in the rear plate.
6. Back off the thumbscrews. Remove the SDR blades and the board. Finish the joints and clean the board.

**Rear plate soldering windows.** There is one window behind each SDR jack column (`SOLDER_WIN_DX` = 7 mm each side of the jack axis in X, `SOLDER_WIN_DZ` = 6 mm above the upper jack and below the lower jack). Each window has a keep-out disc around the board mounting holes (`CLK_HOLE_KEEP` = 4 mm of material). The laser file `rear_plate.dxf` shows the windows.

**SSD bay.** The direct mate moved the clock board 11.3 mm closer to the SDRs. The clock board standoffs are 20 mm long (`CLK_STANDOFF`, was 8 mm), so the rear plate stays at `REAR_Y0` 203.9 mm and the SSD bay stays at `SSD_Y0` 128.4 mm (2026-10-07, option 1). The gap between the NUC rear end and the SSD bay is 20.8 mm: the NUC cables cross there, because the SSD cage fills the full interior height. The SDR rear edge has 46.2 mm to the rear plate for the USB and power plugs. `check_fit.py` finds no clash.

**Check:** `check_fit.py` also prints a mate report. For each SDR and each jack it shows the radial offset between the adapter axis and the jack axis (limit 0.05 mm) and the axial error (the adapter mated face must equal the board jack face +/- 0.01 mm). The mated adapter and board overlap by design, so the clash test skips that pair only.

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
- M3 × 20 mm F-F standoffs ×4 for the clock board (`CLK_STANDOFF`)
- SMP direct mate parts, in total for the four SDRs:
  - 8 Cinch/Johnson 134-1019-451 adapters, SMA plug to SMP female (2 for each SDR). There are no bullets.
  - 8 Amphenol RF SMP-MSSB-PCT10T jacks (board part J3–J10)
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
1a. **SMP stack-up.** Measure `ADAPTER_REACH` on a real B210 with the 134-1019-451 adapter fitted and mated on a jack. The 9.0 mm value is a placeholder. Change the value and rerun. Everything downstream moves. Also check that the engage and disengage forces (15 N maximum and about 22 N typical, for each interface) are safe for the SDR SMA jacks and the board.
1b. **PROPOSED, not confirmed:** `STOP_MATE_MARGIN`, `STOP_ADJ`, `CHAN_CLR`, `RAIL_BASE_GAP`, the rear stop block (`STOP_BLOCK_H`, `STOP_WALL`), the preload boss (`PRE_BOSS_D`, `PRE_BOSS_T`), the window sizes (`SOLDER_WIN_DX`, `SOLDER_WIN_DZ`, `CLK_HOLE_KEEP`) and `STOP_ACCESS_D`. `SSD_REAR_GAP` is confirmed (2026-10-05: the 8 mm smaller NUC cable gap is acceptable). Test the radial and axial repeatability on a prototype first.
2. **Which rear SMAs are 10 MHz and PPS.** The model has three rear SMAs at 14.2, 34.2 and 70.9 mm along the board edge. I've assumed the adjacent pair (14.2 / 34.2) are 10 MHz / PPS; check the silkscreen. If it's a different pair, change `SDR_REAR_SMA` and rerun; the clock strip moves with it.
3. **SSD:**
   - Drive thickness (`SSD_T`, 7 or 9.5 mm).
   - The bottom-hole positions. The sled has slots that cover both common readings.
   - The connector offset `SSD_CONN_C`, and the pocket size for the receptacle adapter you buy.
4. **NUC rear I/O edge.** The model assumes that the edge faces the SSD bay. Check which board edge has the USB 3.0 ports that the SDR cables use.
5. **Drone mount pattern.** Not added yet. Do not use the SSD hatch area of the top plate.
6. **Laser kerf.** Tabs and slots assume 0.15 mm per side. Cut one corner joint first.

## Clock board rev C
The board is in `../clock_board_revC/` (118 × 44 mm, KiCad 10, D-028). Rev C has the rev B outline, holes and jack positions; only the small parts are larger. `heron_rack.py` reads the jack
positions and footprint names from that KiCad file. J3–J10 are SMP jacks (footprint `SMP_Amphenol_SMP-MSSB-PCT10T_Vertical_Float`; the script also accepts the old `SMP-MSLD-PCT` name). J2 is an SMA jack. If the file has an old 132134 footprint on J3–J10, the script still draws them as SMP.
All 8 SDR jacks are coaxial with the SDR reference SMAs (error 0.000 mm).
The board mounts on the rear plate with four M3 × 20 mm standoffs (`CLK_STANDOFF`). Its holes are at world (X, Z) =
(50, 50), (160, 50), (50, 14) and (160, 14).
