"""Make web copies of the website photos.

Team photos arrive as phone files: HEIC (which only Safari can show),
large JPEGs, PNG screenshots. Each photo slot in website/images.json
names its original in ``source``. This tool writes a web copy to
``website/src/img/photos/web/<file>``:

- turned upright (EXIF orientation),
- at most ``max_px`` on the long side (images.json ``web.max_px``, or
  the slot's own ``max_px``),
- JPEG at ``web.quality``, or WebP (``web.figure_quality``) when the
  slot's ``file`` ends in .webp (used for plots, where JPEG blurs text),
- with NO metadata: phone photos carry GPS position and the camera
  serial, which must not go on a public site.

The originals stay where they are and are not published or committed
(see .gitignore). Run it after you add or change a photo:

    python website/tools/prepare_photos.py

HEIC: Pillow cannot read it without the pillow-heif plugin. On Windows
the tool uses the Windows image decoder instead (HEIF Image Extensions,
present on most Windows 10/11 machines), so no extra package is needed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "website" / "images.json"
WEB_DIR = REPO / "website" / "src" / "img" / "photos" / "web"


def heic_to_jpeg_windows(src: Path, dest: Path) -> None:
    """Decode a HEIC file with the Windows imaging component (WIC)."""
    script = (
        "Add-Type -AssemblyName PresentationCore;"
        f"$fs=[IO.File]::OpenRead('{src}');"
        "$d=[Windows.Media.Imaging.BitmapDecoder]::Create($fs,"
        "[Windows.Media.Imaging.BitmapCreateOptions]::None,"
        "[Windows.Media.Imaging.BitmapCacheOption]::OnLoad);"
        "$e=New-Object Windows.Media.Imaging.JpegBitmapEncoder;$e.QualityLevel=95;"
        "$e.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($d.Frames[0].Clone()));"
        f"$o=[IO.File]::Create('{dest}');$e.Save($o);$o.Close();$fs.Close()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)


def open_photo(src: Path) -> Image.Image:
    """Open any slot source, HEIC included."""
    if src.suffix.lower() not in (".heic", ".heif"):
        return Image.open(src)
    try:
        import pillow_heif  # type: ignore[import-not-found]

        pillow_heif.register_heif_opener()
        return Image.open(src)
    except ImportError:
        pass
    if os.name != "nt":
        raise SystemExit(f"{src.name}: install pillow-heif to read HEIC on this system")
    tmp = Path(tempfile.mkdtemp()) / (src.stem + ".jpg")
    heic_to_jpeg_windows(src, tmp)
    return Image.open(tmp)


def main() -> None:
    """Write the web copy of every slot that has a source."""
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    web = data["web"]
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    for name, slot in data["slots"].items():
        if not slot.get("source"):
            continue
        # A source is repo-relative, or absolute for files outside the
        # repository (for example processing reports under D:/01_Data).
        src = Path(slot["source"]) if Path(slot["source"]).is_absolute() else REPO / slot["source"]
        if not src.is_file():
            sys.exit(f"slot {name}: source {slot['source']} is missing")
        im = ImageOps.exif_transpose(open_photo(src)).convert("RGB")
        size = slot.get("max_px", web["max_px"])
        im.thumbnail((size, size), Image.LANCZOS)
        out = WEB_DIR / slot["file"]
        # A new Image with only the pixels: no EXIF, no GPS, no ICC extras.
        clean = Image.new("RGB", im.size)
        clean.paste(im)
        if out.suffix.lower() == ".webp":
            # Figures: WebP keeps thin lines and text sharp at a small size.
            clean.save(out, "WEBP", quality=web.get("figure_quality", 90), method=6)
        else:
            clean.save(out, "JPEG", quality=web["quality"], optimize=True, progressive=True)
        print(f"{name:16s} {src.name} -> web/{out.name} {im.size[0]}x{im.size[1]}")


if __name__ == "__main__":
    main()
