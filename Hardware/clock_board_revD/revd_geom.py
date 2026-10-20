"""Rev D board geometry: where the jacks, the USB holes and the board edges go.

Why one module: the positions come from the SDR and the rack, not from the
circuit. The generator (gen_pcb.py), the silkscreen script and the README all
use these numbers. A change to the rack or to the SDR data is one edit here.

Board-local coordinates: mm, (0, 0) = top-left corner seen from the
component side (the side that faces the SDRs). x to the right, y down.
Rack coordinates: world X = CLK_X0_WORLD + x, world Z = TOP_Z - y.

Sources (also in ../payload_rack/heron_rack.py):
- Z_BOARD = 9.0: height of the SDR board lower edge in the rack.
- Rear-edge connector positions along the SDR board edge (bx, from the
  GPS ANT side), from the Ettus STEP models. Silkscreen order checked on a
  B210 and a B200 (photos, 2026-10-20): GPS ANT 14.22, 10 MHz 34.16,
  USB-B 51.69, PPS 70.89, DC jack 89.43.
- SMA_BZ = 1.96: SMA axis above the SDR board bottom face. The USB-B
  receptacle spans from the board top face (PCB_T = 1.575) to SDR_TOP =
  14.125, so its axis is (1.575 + 14.125) / 2 = 7.85 above the board
  bottom: 5.89 mm from the SMA axis, toward the SDR component side (+x).
"""

# ---------------------------------------------------------------- rack and SDR data
Z_BOARD = 9.0                 # SDR board lower edge (rack z)
BX_GPS, BX_10M, BX_USB, BX_PPS, BX_DC = 14.22, 34.16, 51.69, 70.89, 89.43
SMA_BZ, PCB_T, SDR_TOP = 1.96, 1.575, 14.125
USB_DX = (PCB_T + SDR_TOP) / 2 - SMA_BZ      # 5.89: USB axis offset from the SMA axis (+x)

# ---------------------------------------------------------------- board
# The board spans rack z 30 to z 90. The top edge stays below the SDR DC plugs
# (axis z 98.4) and the bottom edge stays above the GPS ANT plugs (axis z 23.2),
# so both stay usable (user, 2026-10-20). The width is set by the rack: the
# right edge is 2 mm from the rack wall.
TOP_Z, BOT_Z = 90.0, 30.0
BW, BH = 118.0, TOP_Z - BOT_Z
CLK_X0_WORLD = 46.0           # board left edge, rack X (first SDR slot start + 2)

XJ = [13.16, 43.16, 73.16, 103.16]           # SDR SMA axes (30 mm blade pitch)
NAMES = ["B210_1", "B210_2", "B200_1", "B200_2"]
Y_REF = round(TOP_Z - (Z_BOARD + BX_10M), 3)  # 46.84: 10 MHz jack row (J3-J6)
Y_PPS = round(TOP_Z - (Z_BOARD + BX_PPS), 3)  # 10.11: PPS jack row (J7-J10)
Y_USB = round(TOP_Z - (Z_BOARD + BX_USB), 3)  # 29.31: USB hole centre row

# ---------------------------------------------------------------- USB-B holes
# Sized for a generic straight USB 3.0 B overmold, 16 mm along the SDR edge
# by 14 mm across it, with 1 mm clearance each side (user, 2026-10-20). VERIFY
# with the real cable. USB_KEEP is a copper and part keep-out ring around each
# hole, so the hole can grow by up to 2 x USB_KEEP later without new layout.
USB_W, USB_H = 16.0, 18.0     # hole size along x (across the SDR) and y (along its edge)
USB_R = 1.0                   # hole corner radius
USB_KEEP = 2.0                # keep-out ring width


def usb_hole(k):
    """(x0, y0, x1, y1) of the USB hole of SDR k. The last one runs off the
    right board edge, so it is cut as an open notch (see notch_k)."""
    cx = XJ[k] + USB_DX
    return (round(cx - USB_W / 2, 3), round(Y_USB - USB_H / 2, 3),
            round(cx + USB_W / 2, 3), round(Y_USB + USB_H / 2, 3))


def is_notch(k):
    """True when the hole of SDR k leaves less than 3 mm of board at the right edge."""
    return usb_hole(k)[2] > BW - 3.0


def usb_keepout(k):
    """(x0, y0, x1, y1) of the keep-out ring around hole k (clipped to the board)."""
    x0, y0, x1, y1 = usb_hole(k)
    return (x0 - USB_KEEP, y0 - USB_KEEP, min(x1 + USB_KEEP, BW), y1 + USB_KEEP)


# ---------------------------------------------------------------- mounting holes
HOLE_INSET = 4.0              # M3 holes, 4 mm in from each corner (the rack model uses the same)
MOUNT = [(HOLE_INSET, HOLE_INSET), (BW - HOLE_INSET, HOLE_INSET),
         (HOLE_INSET, BH - HOLE_INSET), (BW - HOLE_INSET, BH - HOLE_INSET)]


def to_world(x, y):
    """Board-local mm -> rack (X, Z)."""
    return CLK_X0_WORLD + x, TOP_Z - y
