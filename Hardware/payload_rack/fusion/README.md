# flight_rack Fusion builder

Fusion is the master model (D-035). `heron_rack.py` is frozen. This folder only
builds and checks the Fusion design.

## Files
- `rack_seed.toml` - seed for every User Parameter, the slots and the materials.
  Three names have a trailing underscore (`T_`, `H_`, `W_`) because Fusion refuses
  `T`, `H`, `W` (unit symbols).
- `rack_config.py` - load, validate and evaluate the seed (pure Python).
- `kicad_clock.py` - read the clock board KiCad file (pure Python).
- `fusion_lib.py` - helpers for parameters, sketches, extrudes, joints (needs Fusion).
- `parts/` - one module per part type: `build(ctx)` makes one component.
- `build_flight_rack.py` - part registry `PART_BUILDERS` and placement table `PLACEMENTS`.
- `run_extend.py` - MCP script text: add parts to a saved design (`build_flight_rack.extend`). Refuses other documents and documents with unsaved changes, writes a log, never saves.
- `check_ref.py` - compare Fusion parts with `ref_volumes.json`.
- `sync_kicad.py` - update only the `CLK_J<n>_KX/_KY` parameters.
- `update_revd.py` - MCP script text that brought `flight_rack_asm` to clock board rev D (D-042), in two phases (`params`, `parts`). Writes a log, never saves.
- `fab_plan.py` - laser and print layout plan: part extents as expressions, rows, overlap check (pure Python).
- `fab_layout.py` - build, check and remove the layouts in Fusion (`FAB_laser_layout`, `FAB_print_layout`).
- `run_fab_layout.py` - MCP script text for `fab_layout` (`ACTION` = build, check or remove). Writes a log, never saves.
- `export_laser_dxf.py` - MCP script text: save the laser DXF files (one per part type plus the whole layout sheet) to `../out/fusion_laser_dxf/`. No kerf compensation (Q-016). Never saves the design.
- `run_in_fusion.py` - loader text for the Fusion MCP script tool.
- `tests/` - pytest for the pure-Python modules.

## Run in Fusion
Copy `run_in_fusion.py` into the MCP script call. Set `ENTRY` and `FUNC`
(`build_flight_rack` / `main` builds into a NEW untitled document). Close the
document without saving when you are done.

## Run the tests
`<cq-venv python> -m pytest Hardware/payload_rack/fusion/tests`

## Rules that the helpers keep
- Lengths are expression strings ("W_ + T_"). Never raw cm numbers.
- Every sketch entity is fixed by dimensions that use parameters. The sketch
  is drawn on the correct side first, with a tiny offset (<= 0.1 mm) that the
  dimensions remove. Dimensions are unsigned: a parameter must not change sign.
- CadQuery world Z + `RACK_Z0` = Fusion world Z (the bottom plate is grounded at the origin).
- Pattern counts (`*_NX`, `*_NY`) are read at build time. A count of 1 makes no
  pattern in that direction. Changing a count later needs a rebuild of that part.

## Blades (phase 3a)
- `parts/tray.py` builds `LZR_tray_sdr` and `LZR_tray_nuc`. `parts/blade.py` builds
  `BLADE_sdr` and `BLADE_nuc`: one tray, a bottom TPU rail and a top TPU rail, with
  rigid joints inside the blade. The top rail is the SAME rail component turned 180 deg
  about Y (the rail profile is symmetric in x, so this equals `mirror_top`).
- Each blade occurrence has a slider joint along world Y to a root joint origin
  (`JO_<slot>` at `TRAY_X_<slot>`, `RACK_Z0`). Slide value 0 is the installed position.
- `build_subset(design, parts, labels)` builds only some parts. `extend(design, parts, labels)`
  works on a built design: it creates missing User Parameters only (Fusion is master),
  builds missing parts, adds missing placements, and deletes the old root occurrences of
  the tray and rails (the blade nests new occurrences of the same components).
- Fusion fault: a joint that moves a nested occurrence of the FIRST component of a
  document fails ("conflicts with assembly relationships"). The real design has the
  bottom plate first, so this does not occur there. A dev document needs a first dummy part.

## Rail strips (phase 3b)
- `parts/strip.py` builds `ASA_rail_strip_<sdr|nuc>_<bot|top>` with one function and a `top` flag.
  The `_top` strip is the bottom strip mirrored about its local XY plane (z -> -z), NOT a rotation.
  The flag only flips the sign of the Z offsets and the extrude direction.
- `build_flight_rack.strip_rows` makes 10 rigid placements (`strip_bot_<slot>`, `strip_top_<slot>`).
  Bottom origin: `(TRAY_X_<slot>, 0, RACK_Z0)`. Top origin: `(TRAY_X_<slot>, 0, H_ + RACK_Z0)`, identity frame.
- `run_extend.py` is the MCP text that adds the strips to `flight_rack_asm` (log: `extend_strips.log`).

## Swing latches (phase 4a)
- `parts/latch.py` builds `ASA_latch_sdr_preload` (boss, insert pocket, screw hole) and
  `ASA_latch` (plain). The latch is symmetric about its local y = 0, so `mirror_top` of a latch is
  a move in Z (by `H_ - 2 * LATCH_Z`), not a mirror image. The top latches are more occurrences of
  the same two components (D-035, 2026-10-14), as in the print list (8 preload, 3 plain).
- `build_flight_rack.latch_rows` makes 11 revolute placements: `latch_bot_<slot>`, `latch_top_<slot>`
  and `latch_ssd_hatch` (an occurrence of `ASA_latch` on the top plate, axis world Z). The child joint
  origin is `JO_pivot` (pivot hole centre). The parent origin lies on the pivot screw axis. Angle 0 is the
  CadQuery position. There are no angle limits (none are specified).
- The pivot offset from the tray is `STRIP_W / 2 + EAR_W / 2` (the same as the strip ear insert). heron_rack.py
  writes the literal 3.0 there. They agree while `EAR_W` is 6.
- `run_extend.py` is the MCP text that adds the latches to `flight_rack_asm` (log: `extend_latches.log`).

## SSD bay (phase 4b)
- `parts/ssd_cage.py` builds `ASA_ssd_cage` and `parts/ssd_sled.py` builds `ASA_ssd_sled`. Both use WORLD
  (CadQuery) axes, because heron_rack.py has no local function for them. The joint offset is the identity plus
  `RACK_Z0` in Z. New seed parameters: the cage face values (`SSD_CX0/CX1/OX0/OX1/FY0/FY1/PC/CCY`) and five
  literals (`SSD_POCKET_Y_OVER`, `SSD_LIGHTEN_X_OVER`, `SSD_LIGHTEN_Z0`, `SSD_FLANGE_OVER`, `SLED_TOP_GAP`).
- `build_flight_rack.ssd_rows` makes 2 placements: `ssd_cage` (rigid) and `ssd_sled` (slider along world Z,
  slide 0 = installed, no travel limit because none is specified). Both parent joint origins sit at `(0, 0, RACK_Z0)`.
- On the YZ plane Fusion puts sketch (x, y) at world (-Z, Y). The sled cuts use offset YZ planes and `flip_range`.
- Change from heron_rack.py (D-035, 2026-10-14): in heron_rack.py the two top ears of the cage touch no other
  part of the cage (a 4.9 mm gap to each guide), so it is 3 loose solids. In Fusion the top ears span the full
  cage length (`SSD_EAR_Y0` to `SSD_EAR_Y1` = `SSD_FY0` to `SSD_FY1`) and join both guides: the cage is one body,
  2454 mm3 larger. The lightening cut still nicks the bottom flanges, as in CadQuery.
- Removal path: the sled lifts 110 mm without interference with the top plate, the cage and the hatch latch.
- `run_extend.py` is the MCP text that adds the SSD bay to `flight_rack_asm` (log: `extend_ssd.log`).

## Reference envelopes (phase 5)
- Category `ref`: not made, no material, translucent green (`[ref_look]` in the seed: library appearance
  `Plastic - Translucent Matte (Green)`, colour 0.2/0.55/0.3, body opacity 0.6; the CadQuery ref colour).
  `apply_materials.py` skips `ref` (its `MATERIAL_CATEGORIES` has only laser, rigid, flex). Keep it so.
- `parts/clock_board_ref.py` builds `REF_clock_board_revD` (mirror of `clock_board_ref()`): PCB with 4 holes, J1 JST box,
  J3-J10 SMP jacks (square body, flange, shroud ending at `SMP_JACK_H`), J2 SMA (body and barrel). Local frame = the rear plate
  frame, origin on the board REAR face. Jack positions use `CLK_J<n>_X` / `_Z`, so `sync_kicad.py` moves them.
  Which J is SMP or SMA, and the J list, come from `kicad_clock.jack_groups` at build time (static).
- `parts/ref_parts.py` builds `REF_smp_adapter` (one cylinder `ADAPTER_D` x `ADAPTER_REACH`), `REF_SDR_envelope`,
  `REF_NUC_envelope` and `REF_SSD`.
- `build_flight_rack.ref_rows` makes 11 rigid placements at the ROOT: `REF_clock_board_revD` (origin `CLK_Y0 + CLK_PCB_T`),
  8 adapters `REF_adapter_<slot>_<ref_a|ref_b>` (origin on the SDR SMA axis at `SDR_SMA_TIP_Y`, cylinder toward +Y),
  `REF_NUC_envelope` and `REF_SSD`. New seed value: `NUCREF_INSET`.
- SDR board stand-ins (added 2026-10-26, D-048): one component `REF_SDR_envelope` (one box `SDR_W` x `SDR_L` x
  `SDR_TOP`, the same as the `heron_rack.py` fallback, because the Ettus STEP files are missing), placed 4 times at
  the ROOT: `REF_B210_1 ... REF_B200_2`, origin `TRAY_X_<slot> + T_ / 2 + STANDOFF`, frame like the NUC. No new
  parameter. Check 2026-10-26: X span 14.125 mm from the board plane, Y 3.0-157.7, Z 12.0-112.0 in all 4 slots;
  interference with the made parts 0. Like the NUC, they are fixed at the root and do not slide with the blades.
- Open question (not decided): should `REF_NUC_envelope` sit inside `BLADE_nuc`, and should `REF_SSD` follow the sled?
  Now both are fixed at the root. The adapters are also fixed at the root (they do not slide with the blades).
- `run_extend.py` is the MCP text that adds the refs to `flight_rack_asm` (log: `extend_refs.log`; the SDR run:
  `extend_sdr_refs.log`).

## Clock board rev D update (2026-10-20, D-040, D-042)
- `update_revd.py` ran on `flight_rack_asm` (not saved by the script). Phase `params`: set `CLK_H` 60, `CLK_TOP_Z` 90,
  `SDR_REAR_SMA_REF_A` 34.16 (10 MHz), `SDR_REAR_SMA_REF_B` 70.89 (PPS) and the J1 XH envelope (`CLKREF_JST_W/H/D`
  7.4/11.5/6.1); created `SDR_GPS_BX`, `USB_DX`, `CLK_USB_W/H`, `REAR_USB_W/H`, `CLKREF_JST_DX/DZ`; deleted the unused
  `SDR_REAR_SMA_AUX`; set the 20 jack parameters from the rev D KiCad file; suppressed the rear plate `windows_above`
  grid (its window is only 6 mm tall now; heron_rack.py drops that grid too). Phase `parts`: replaced
  `REF_clock_board_revC` with `REF_clock_board_revD` (USB holes, J1 offset) and added `usb_openings` to `LZR_rear_plate`.
- Checks after the update: no unhealthy timeline item (only the 3 suppressed `windows_above` items); board bbox
  X 46-164, Z 30-90 (CadQuery frame); all 8 adapter axes on their jacks; volumes equal CadQuery (board 11187 mm3,
  rear plate 44332 mm3); Fusion interference analysis of all root occurrences: 0.
- `heron_rack.py` (frozen, D-035) was also edited for the J1 envelope and rev D, so `check_fit.py` keeps working. Fusion
  stays the master.
- Phase `holes` (2026-10-21, D-043): rebuilt `REF_clock_board_revD` with 6 mounting holes and added `clock_center_holes`
  (the 2 mid-edge holes) to `LZR_rear_plate`. Volumes equal CadQuery (board 11161 mm3, rear plate 44278 mm3), interference 0.

## Fabrication layouts (2026-10-21, D-044)
- `FAB_laser_layout` holds all 10 laser pieces flat (thickness along Z, lowest face on Z = 0). `FAB_print_layout`
  holds all 33 printed pieces in their local frames. Both sit at +X beside the rack (`FAB_SPACE`); parts are
  `FAB_GAP` apart. Each piece is an occurrence of the rack component, so an edit to a part shows in the layout.
  The joint offsets are expressions of the part extents (`fab_plan.EXTENTS`), so the spacing follows the parts.
- Sheet thickness: `T_` drives every laser part (plates, walls, rear plate, trays) and the parts that fit them
  (slots, TPU rail lips, latch plates). Tested: `T_` 3 -> 5 -> 3 mm, layouts stay clear, no new timeline problems.
- Fusion fault: every occurrence of the first component (`LZR_bottom_plate`) is pinned at the origin. Joints,
  transforms and position snapshots all snap back, and Arrange refuses it ("pinned"). The bottom plate piece is a
  linked body copy (CopyPasteBody, tracks the source) moved by an expression Move feature.
- Arrange (Design workspace) is not used: it refuses these parts as pinned, and the per-part quantity needs a
  Fusion extension.
- A new part feature that grows a part outside its `fab_plan.EXTENTS` box can make pieces overlap. Run the
  `check` action after such an edit. A change to a piece count needs `remove`, an edit of `fab_plan.LAYOUTS`
  and `build`.
- Export: laser - sketch on the layout XY plane, project the pieces, right-click the sketch, Save As DXF
  (1:1 mm, for CorelDRAW). Print - right-click a component, Save As Mesh (STL or 3MF). The `_top` rail strips
  are already mirrored: do not mirror them in the slicer. Choose the print orientation in the slicer.
- No laser beam compensation is in the model (Q-016). `KERF` is only the slot clearance on each side.
- The layout copies add to the root mass and to a Fusion BOM.
