# HERON payload rack, Rev A (concept)

A blade-style rack for 2× B210, 2× B200, the bare NUC7i3DNB board, a hot-swap 2.5" SATA SSD, and the clock board. It is made from laser-cut sheet plus printed ASA and TPU, with no metal structure and no fan.

**Envelope:** 172 W × 219 D × 126 H mm (front = RF side). Rack mass without boards is about 0.47 kg in 3 mm plywood plus printed parts. That excludes screws, inserts and standoffs (about 40 g) and the copper tape (about 25 g).

## How it works
- **Blades.** Each module screws to a 3 mm laser-cut tray through its own mounting holes. A TPU C-rail clips onto the tray's top edge and another onto its bottom edge.
  - The four SDR trays are solid. Each carries a grounded copper-tape shield (see below). The NUC tray is skeletal.
  - The rails slide in printed ASA rail strips bolted to the top and bottom plates.
  - Any blade pulls out the front on its own: swing its two latches aside and pull the finger tab.
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
  - Its 8 SMA jacks face forward, coaxial with each SDR's rear reference SMAs.
  - Use 50–75 mm flexible RG316 male–male jumpers.

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
- latch ×11 (10 for the blades, 1 for the SSD hatch)
- SSD cage ×1, SSD sled ×1

**Print, TPU 95A (100%):** SDR C-rail ×8, NUC C-rail ×2.

**Hardware:**
- M3 heat-set inserts (about 40)
- M3 screws plus square nuts for the T-slot joints (16)
- M3 × 6 mm F-F standoffs: 16 **metal** for the SDRs (they ground the copper tape), plus nylon for the NUC
- M3 flat-head (countersunk) screws ×4 for the SSD sled
- copper tape with conductive adhesive (about 0.2 m² for 4 trays) and Kapton tape
- a short cord or ribbon pull loop for the SSD sled handle
- M3 × 8 mm standoffs ×4 for the clock board
- 8 SMA jumpers
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
positions from that KiCad file. All 8 jacks are coaxial with the SDR reference SMAs (error 0.000 mm).
The board mounts on the rear plate with four M3 × 8 mm standoffs. Its holes are at world (X, Z) =
(50, 50), (160, 50), (50, 14) and (160, 14).
