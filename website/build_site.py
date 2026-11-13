"""Build herongnss.org into one folder of static files.

The site has three parts (decisions D-102 to D-105):

- ``/``, ``/about/``: hand-written pages in ``website/src``. Each page
  takes the shared head, top bar and footer from ``src/_partials``.
- ``/course/``: the GNSS course, built by ``docs/teaching/build.py``.
- ``/sim/``: the REAL ground station page (heron_base/web/index.html),
  copied unchanged except for three lines in <head> that load the
  in-browser simulation (``website/sim``). So the public page is always
  the page the team flies with.

Draft gate: a claim that the team must check is marked
``class="review"``. The build counts the markers. With ``--final`` it
fails while any are left; the GitHub Actions workflow deploys only a
build with zero markers. So nothing unreviewed goes live.

Usage (standard library only), from the repository root:

    python website/build_site.py            # build website/_site
    python website/build_site.py --final    # fail if a review marker is left
    python website/build_site.py --serve    # build, then serve on localhost
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SETTINGS = Path(__file__).resolve().parent / "site.json"
REVIEW = re.compile(r'class="review"')
# The sim needs these strings in the real page. A missing one means the
# dashboard changed in a way the simulation does not know.
DASHBOARD_HOOKS = ("</head>", 'fetch("/api/state"', 'fetch("/api/command"')


def load_settings() -> dict:
    """Read site.json and check the keys the build needs."""
    data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    need = ["domain", "out_dir", "pages_src", "course_build", "dashboard_page", "sim_dir"]
    missing = [k for k in need + ["sim_files", "sim_title"] if k not in data]
    if missing:
        raise SystemExit(f"{SETTINGS}: missing keys {missing}")
    return data


def render_page(src: Path, src_root: Path, partials: dict[str, str], draft: bool) -> str:
    """Fill the <!--HEAD-->, <!--TOP--> and <!--FOOT--> markers of one page.

    ROOT is the relative path back to the site root, so the site works
    from any folder (a local preview or the real domain).
    """
    depth = len(src.relative_to(src_root).parts) - 1
    root = "../" * depth
    section = src.relative_to(src_root).parts[0] if depth else ""
    text = src.read_text(encoding="utf-8")
    for marker, key in (("<!--HEAD-->", "head"), ("<!--TOP-->", "top"), ("<!--FOOT-->", "foot")):
        text = text.replace(marker, partials[key])
    bar = '<div class="draftbar">DRAFT: this site is under team review.</div>' if draft else ""
    text = text.replace("{{DRAFTBAR}}", bar).replace("{{ROOT}}", root)
    for name in ("about", "course", "sim"):
        text = text.replace(
            f"{{{{CUR_{name}}}}}", ' aria-current="page"' if name == section else ""
        )
    return text


def build_pages(cfg: dict, out: Path) -> int:
    """Render the hand-written pages. Return the number of review markers."""
    src_root = REPO / cfg["pages_src"]
    parts_dir = src_root / "_partials"
    partials = {
        k: (parts_dir / f"{k}.html").read_text(encoding="utf-8") for k in ("head", "top", "foot")
    }
    pages = [p for p in src_root.rglob("*") if p.is_file() and "_partials" not in p.parts]
    draft = any(REVIEW.search(p.read_text(encoding="utf-8")) for p in pages if p.suffix == ".html")
    count = 0
    for src in pages:
        dest = out / src.relative_to(src_root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix == ".html":
            text = render_page(src, src_root, partials, draft)
            count += len(REVIEW.findall(text))
            dest.write_text(text, encoding="utf-8")
        else:
            shutil.copy2(src, dest)
    return count


def build_course(cfg: dict, out: Path) -> None:
    """Build the course straight into /course/index.html."""
    dest = out / "course" / "index.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, str(REPO / cfg["course_build"]), "--out", str(dest)], check=True
    )


def build_sim(cfg: dict, out: Path) -> None:
    """Copy the real dashboard page and add the simulation scripts."""
    page = (REPO / cfg["dashboard_page"]).read_text(encoding="utf-8")
    missing = [h for h in DASHBOARD_HOOKS if h not in page]
    if missing:
        raise SystemExit(
            f"the dashboard page changed; the sim needs {missing}. Update website/sim."
        )
    inject = (
        '<link rel="stylesheet" href="sim.css">\n'
        '<script src="heron_sim.js"></script>\n'
        '<script src="sim_shim.js"></script>\n'
    )
    page = page.replace("</head>", inject + "</head>", 1)
    page = re.sub(r"<title>.*?</title>", f"<title>{cfg['sim_title']}</title>", page, count=1)
    dest = out / "sim"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "index.html").write_text(page, encoding="utf-8")
    for name in cfg["sim_files"]:
        shutil.copy2(REPO / cfg["sim_dir"] / name, dest / name)


def build(final: bool) -> tuple[Path, int]:
    """Build the whole site. Return the output folder and the marker count."""
    cfg = load_settings()
    out = REPO / cfg["out_dir"]
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    reviews = build_pages(cfg, out)
    build_course(cfg, out)
    build_sim(cfg, out)
    # GitHub Pages: the custom domain, and no Jekyll processing.
    (out / "CNAME").write_text(cfg["domain"] + "\n", encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    print(f"built {out} with {reviews} review marker(s)")
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"reviews={reviews}\n")
    if final and reviews:
        raise SystemExit(f"{reviews} review marker(s) left; the site is not ready to publish")
    return out, reviews


def serve(folder: Path, port: int) -> None:
    """Serve the built site for a local check."""
    import functools
    import http.server

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(folder))
    print(f"serving http://localhost:{port}/  (Ctrl+C to stop)")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()


def main() -> None:
    """Parse arguments, build, and optionally serve."""
    ap = argparse.ArgumentParser(description="Build herongnss.org.")
    ap.add_argument("--final", action="store_true", help="fail if a review marker is left")
    ap.add_argument("--serve", action="store_true", help="serve the result on localhost")
    ap.add_argument("--port", type=int, default=8766)
    args = ap.parse_args()
    out, _ = build(args.final)
    if args.serve:
        serve(out, args.port)


if __name__ == "__main__":
    main()
