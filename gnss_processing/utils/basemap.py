"""
Satellite imagery basemaps for a local East/North plot.

Deliberately not `contextily`: that pulls in `rasterio` (and so GDAL), a
dependency chain that is often the least reliable part of a Windows
scientific-Python setup -- exactly the kind of DLL/binary-wheel fragility this
project has already run into more than once. Everything this module needs
(`requests`, `numpy`, `PIL`) is already a dependency of something else here,
so there is nothing new to install.

The approach: fetch the standard Web Mercator "slippy map" tiles that cover
the requested area from a public, key-less imagery server, stitch them into
one image, and report that image's extent in the same LOCAL EAST/NORTH METRES
`utils.plotting.plot_position_enu`'s scatter already plots in -- so it can be
handed straight to `ax.imshow(image, extent=...)` underneath the existing
fixes with no change to how they are plotted.

**The one approximation worth naming.** Converting a reference lat/lon to
local east/north offsets uses the same small-area flat-Earth approximation
implicit in an ENU frame: `north_m = lat_offset_rad * R`,
`east_m = lon_offset_rad * R * cos(ref_lat)`. This is what keeps the tile
image's extent numerically consistent with the ENU coordinates the position
fixes are already plotted in (both are local-tangent-plane approximations
about the same reference point), rather than introducing a second, competing
notion of "flat" that would misalign the two. It is accurate to within
centimetres over the few-hundred-metre to few-kilometre spans this is for;
it is not meant for anything continental.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import requests

from . import environment_variables

# ArcGIS Online's World Imagery: free, no API key or account needed, and the
# same default `contextily` itself reaches for. `{z}/{y}/{x}` -- note the
# order -- with no file extension; the response's own Content-Type says JPEG.
ESRI_WORLD_IMAGERY_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)

TILE_PIXELS = 256
EARTH_RADIUS_M = 6_378_137.0  # WGS84 semi-major axis; Web Mercator's own radius

# ArcGIS's own service limit; nothing here should approach it, but a mistaken
# huge half_extent_m should fail loudly rather than silently fetch thousands
# of tiles.
MAX_TILES = 64


def _lonlat_to_tile_frac(lon_deg: float, lat_deg: float, zoom: int) -> tuple[float, float]:
    """Fractional (tile_x, tile_y) for a lon/lat at a Web Mercator zoom level."""
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    x = (lon_deg + 180.0) / 360.0 * n
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def _tile_to_lonlat(tile_x: float, tile_y: float, zoom: int) -> tuple[float, float]:
    """Inverse of `_lonlat_to_tile_frac`: the lon/lat at a tile corner."""
    n = 2.0 ** zoom
    lon_deg = tile_x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1.0 - 2.0 * tile_y / n)))
    return lon_deg, math.degrees(lat_rad)


def _lonlat_to_local_m(
    lon_deg: float, lat_deg: float, ref_lat_deg: float, ref_lon_deg: float
) -> tuple[float, float]:
    """(east_m, north_m) of a lon/lat relative to a reference point -- see the
    module docstring for why this specific approximation is the right one here."""
    north_m = math.radians(lat_deg - ref_lat_deg) * EARTH_RADIUS_M
    east_m = (
        math.radians(lon_deg - ref_lon_deg) * EARTH_RADIUS_M * math.cos(math.radians(ref_lat_deg))
    )
    return east_m, north_m


def choose_zoom(half_extent_m: float, ref_lat_deg: float, *, target_pixels: int = 900) -> int:
    """
    The zoom level giving roughly `target_pixels` across the requested span.

    Ground resolution at zoom z is `156543.03 * cos(lat) / 2**z` m/pixel (the
    standard Web Mercator formula -- 156543.03 is the equatorial circumference
    divided by 256, the tile size). Solved for z given the desired metres per
    pixel, then clamped to what ArcGIS actually serves.
    """
    span_m = 2.0 * half_extent_m
    meters_per_pixel = max(span_m / target_pixels, 0.01)
    z = math.log2(156543.03392 * math.cos(math.radians(ref_lat_deg)) / meters_per_pixel)
    return int(np.clip(round(z), 1, 19))


@dataclass(frozen=True)
class Basemap:
    """A stitched satellite image, and the local east/north extent it covers."""

    image: np.ndarray
    """(H, W, 3) uint8 RGB."""

    extent_m: tuple[float, float, float, float]
    """(east_min, east_max, north_min, north_max), for `ax.imshow(..., extent=...)`."""

    zoom: int


def fetch_tile(
    tile_x: int, tile_y: int, zoom: int,
    *, tile_url: str = ESRI_WORLD_IMAGERY_URL, cache_dir: str | Path | None = None,
    timeout_s: float = 15.0,
) -> np.ndarray:
    """
    One tile's RGB pixels, as a `(256, 256, 3)` uint8 array.

    Cached to disk exactly like `utils.cddis.download` caches its own fetches:
    a notebook re-run should not re-download tiles it already has, and a
    satellite basemap does not change day to day the way an ephemeris does.
    """
    from PIL import Image

    if cache_dir is None:
        cache_dir = environment_variables.get_resources_path() / "basemap_tiles"
    cache_path = Path(cache_dir) / f"z{zoom}" / f"{tile_x}_{tile_y}.jpg"

    if cache_path.exists():
        with Image.open(cache_path) as img:
            return np.asarray(img.convert("RGB"))

    url = tile_url.format(z=zoom, x=tile_x, y=tile_y)
    response = requests.get(url, timeout=timeout_s)
    response.raise_for_status()
    with Image.open(io.BytesIO(response.content)) as img:
        rgb = img.convert("RGB")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        rgb.save(cache_path, format="JPEG", quality=90)
        return np.asarray(rgb)


def fetch_satellite_basemap(
    center_lat_deg: float,
    center_lon_deg: float,
    half_extent_m: float,
    *,
    zoom: int | None = None,
    tile_url: str = ESRI_WORLD_IMAGERY_URL,
    cache_dir: str | Path | None = None,
) -> Basemap:
    """
    Satellite imagery covering `half_extent_m` metres each direction of
    `(center_lat_deg, center_lon_deg)`, in local east/north metres about that
    same centre -- ready to hand to `utils.plotting.plot_position_enu`'s
    `basemap` argument, or straight to `ax.imshow(basemap.image,
    extent=basemap.extent_m)`.

    Raises rather than silently truncating if the requested area would need
    more than `MAX_TILES` tiles -- almost always a `half_extent_m` given in
    the wrong units (km meant, m given) rather than a genuinely huge area.
    """
    if zoom is None:
        zoom = choose_zoom(half_extent_m, center_lat_deg)

    cx, cy = _lonlat_to_tile_frac(center_lon_deg, center_lat_deg, zoom)
    # How many tiles the requested span needs, in tile units: reuse the same
    # ground-resolution formula `choose_zoom` inverts, now forward. No extra
    # margin added here -- flooring/ceiling `cx +/- half_span_tiles` to whole
    # tile boundaries below already rounds OUTWARD, so the mosaic is
    # guaranteed to cover at least `half_extent_m` on its own; adding a margin
    # on top of that only makes the result surprisingly larger than asked for
    # (a whole extra tile is a lot of ground at a coarse zoom).
    meters_per_pixel = 156543.03392 * math.cos(math.radians(center_lat_deg)) / (2.0 ** zoom)
    half_span_tiles = half_extent_m / meters_per_pixel / TILE_PIXELS

    x0 = int(math.floor(cx - half_span_tiles))
    x1 = int(math.floor(cx + half_span_tiles))
    y0 = int(math.floor(cy - half_span_tiles))
    y1 = int(math.floor(cy + half_span_tiles))
    n_tiles_side = 2 ** zoom

    num_tiles = (x1 - x0 + 1) * (y1 - y0 + 1)
    if num_tiles > MAX_TILES:
        raise ValueError(
            f"covering +/-{half_extent_m:g} m at zoom {zoom} needs {num_tiles} tiles, "
            f"over the {MAX_TILES} limit -- half_extent_m is probably in the wrong "
            "units, or pass an explicit (coarser) zoom"
        )

    mosaic = np.zeros(((y1 - y0 + 1) * TILE_PIXELS, (x1 - x0 + 1) * TILE_PIXELS, 3), dtype=np.uint8)
    for ty in range(y0, y1 + 1):
        for tx in range(x0, x1 + 1):
            # Web Mercator tiles do not wrap in y (no imagery past +/-85.05
            # degrees), but do in x; wrap rather than error for a span crossing
            # the antimeridian, which is otherwise a plausible request.
            tile = fetch_tile(tx % n_tiles_side, ty, zoom, tile_url=tile_url, cache_dir=cache_dir)
            row0 = (ty - y0) * TILE_PIXELS
            col0 = (tx - x0) * TILE_PIXELS
            mosaic[row0 : row0 + TILE_PIXELS, col0 : col0 + TILE_PIXELS] = tile

    lon0, lat0 = _tile_to_lonlat(x0, y0, zoom)             # top-left corner
    lon1, lat1 = _tile_to_lonlat(x1 + 1, y1 + 1, zoom)     # bottom-right corner
    east0, north0 = _lonlat_to_local_m(lon0, lat0, center_lat_deg, center_lon_deg)
    east1, north1 = _lonlat_to_local_m(lon1, lat1, center_lat_deg, center_lon_deg)

    # Tile y increases southward (like image rows), so north0 (top) > north1
    # (bottom); imshow's default origin="upper" expects extent as
    # (left, right, bottom, top), i.e. the smaller north value first.
    return Basemap(
        image=mosaic,
        extent_m=(east0, east1, north1, north0),
        zoom=zoom,
    )
