"""Build the HERON GNSS course into one self-contained HTML file.

Why one file: the team must open the course with no network and no
server, and the same file must publish as a claude.ai Artifact. So
this script inlines the styles, the KaTeX library and fonts, the
HERON data, and every section into one page.

Usage (standard library only):

    python docs/teaching/build.py                 # writes docs/teaching/gnss_course.html
    python docs/teaching/build.py --fragment OUT  # writes a body-only copy for Artifact publishing

To add a part: add a file pair src/sections/<id>.html and src/js/<id>.js,
then add one row to PARTS below. The order of PARTS is the lecture order.
"""

from __future__ import annotations

import argparse
import base64
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"

# (section id, short title for the table of contents, planned minutes)
PARTS = [
    ("s01", "Trilateration", 12),
    ("s02", "What GNSS needs", 4),
    ("s03", "Signal structure", 6),
    ("s04", "Codes and correlation", 10),
    ("s05", "Navigation message to fix", 6),
    ("s06", "Acquisition", 12),
    ("s07", "Tracking loops", 18),
    ("s08", "HERON signal plan", 8),
    ("s09", "Pseudorange model", 6),
    ("s10", "GNSS-R and open loop", 8),
]


def katex_css_inline() -> str:
    """Return the KaTeX CSS with each font as a woff2 data URI.

    KaTeX lists woff2, woff and ttf sources. Browsers that run this page
    all read woff2, so the script keeps only woff2 to save space.
    """
    vend = SRC / "vendor" / "katex"
    css = (vend / "katex.min.css").read_text(encoding="utf-8")

    def repl(m: re.Match) -> str:
        name = m.group(1)
        data = base64.b64encode((vend / "fonts" / f"{name}.woff2").read_bytes()).decode()
        return f'src:url(data:font/woff2;base64,{data}) format("woff2")'

    pattern = (
        r'src:url\(fonts/([A-Za-z0-9_\-]+)\.woff2\) format\("woff2"\)'
        r'(?:,url\([^)]*\) format\("[a-z]+"\))*'
    )
    return re.sub(pattern, repl, css)


def build(fragment: bool) -> str:
    """Assemble the page text from the template and the parts."""
    page = (SRC / "index.html").read_text(encoding="utf-8")
    toc, agenda, mob, sections, scripts = [], [], [], [], []
    for i, (sid, title, minutes) in enumerate(PARTS, start=1):
        if not (SRC / "sections" / f"{sid}.html").exists():
            print(f"warning: part {sid} has no HTML file; it is left out")
            continue
        toc.append(
            f'<li><a href="#{sid}"><span class="n">{i:02d}</span>'
            f'<span>{title}</span><span class="m">{minutes}′</span></a></li>'
        )
        agenda.append(f"<span>{i:02d}</span><span>{title}</span><span>{minutes} min</span>")
        mob.append(f'<a class="btn" href="#{sid}">{i:02d} {title}</a>')
        html = (SRC / "sections" / f"{sid}.html").read_text(encoding="utf-8")
        html = html.replace("{{NUM}}", f"{i:02d}").replace("{{MIN}}", str(minutes))
        sections.append(html)
        js = SRC / "js" / f"{sid}.js"
        if js.exists():
            scripts.append(f"<script>\n{js.read_text(encoding='utf-8')}\n</script>")
    total = sum(p[2] for p in PARTS)
    agenda.append(f"<span></span><span><b>Total</b></span><span>{total} min</span>")

    vend = SRC / "vendor" / "katex"
    data = (SRC / "data" / "heron_data.json").read_text(encoding="utf-8")
    # A JSON string cannot hold "</", but escape it anyway so the script tag stays closed.
    data = data.replace("</", "<\\/")
    swaps = {
        "/*KATEX_CSS*/": katex_css_inline(),
        "/*COURSE_CSS*/": (SRC / "css" / "course.css").read_text(encoding="utf-8"),
        "<!--TOC-->": "\n".join(toc),
        "<!--AGENDA-->": "\n".join(agenda),
        "<!--MOBTOC-->": "".join(mob),
        "<!--SECTIONS-->": "\n".join(sections),
        "/*HERON_DATA*/": data,
        "/*KATEX_JS*/": (vend / "katex.min.js").read_text(encoding="utf-8"),
        "/*KATEX_AUTORENDER*/": (vend / "auto-render.min.js").read_text(encoding="utf-8"),
        "/*CORE_JS*/": (SRC / "js" / "core.js").read_text(encoding="utf-8"),
        "<!--SECTION_SCRIPTS-->": "\n".join(scripts),
    }
    for key, val in swaps.items():
        if key not in page:
            raise SystemExit(f"template marker {key} is missing from src/index.html")
        page = page.replace(key, val, 1)
    if fragment:
        return page
    head, _, body = page.partition('<div class="shell">')
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        f'{head}</head>\n<body>\n<div class="shell">{body}\n</body>\n</html>\n'
    )


def main() -> None:
    """Parse arguments and write the output file."""
    ap = argparse.ArgumentParser(description="Build the HERON GNSS course page.")
    ap.add_argument("--out", type=Path, default=ROOT / "gnss_course.html")
    ap.add_argument(
        "--fragment", type=Path, help="also write a body-only copy for Artifact publishing"
    )
    args = ap.parse_args()
    args.out.write_text(build(False), encoding="utf-8")
    print(f"wrote {args.out} ({args.out.stat().st_size / 1e6:.2f} MB)")
    if args.fragment:
        args.fragment.write_text(build(True), encoding="utf-8")
        print(f"wrote {args.fragment}")


if __name__ == "__main__":
    main()
