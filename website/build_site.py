"""Build herongnss.org into one folder of static files.

The site has three parts (decisions D-116 to D-122):

- ``/``, ``/about/``: hand-written pages in ``website/src``. Each page
  takes the shared head, top bar and footer from ``src/_partials``.
- ``/course/``: the GNSS course, built by ``docs/teaching/build.py``.
- ``/sim/``: Jack Abrams' REAL mission planner (Specular_Planner), its
  page copied unchanged except for a few lines in <head>. Its Python
  backend (app.py and engine/) runs in the browser with Pyodide; see
  ``website/planner/``. The planner source comes from a git ref
  (site.json ``planner.git_ref``), so the site follows Jack's branch.

Draft gate: a claim that the team must check is marked
``class="review"``. The build counts the markers. With ``--final`` it
fails while any are left; the GitHub Actions workflow deploys only a
build with zero markers. So nothing unreviewed goes live.

Usage (standard library only), from the repository root:

    python website/build_site.py            # build website/_site
    python website/build_site.py --final    # fail if a review marker is left
    python website/build_site.py --serve    # build, then serve on localhost
    python website/build_site.py --planner-deps DIR   # use these site-packages

The planner needs a few pure-Python packages (planner/requirements-
pyodide.txt). Without ``--planner-deps`` the build installs them with
pip into website/_cache (this needs the network, as in CI).
"""

from __future__ import annotations

import argparse
import html
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
# Files of the planner that the browser needs. Static files are served;
# the rest go in the bundle for Pyodide.
PLANNER_CODE = ("Specular_Planner/app.py", "Specular_Planner/engine", "Specular_Planner/data")
PAYLOAD_CODE = "HERON_Payload/heron_payload"
SKIP = re.compile(r"(__pycache__|\.pyc$|\.pyd$|\.so$|\.dll$|\.exe$|\.dist-info|\.egg-info)")


def load_settings() -> dict:
    """Read site.json and check the keys the build needs."""
    data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    need = [
        "domain",
        "out_dir",
        "pages_src",
        "images",
        "course_build",
        "planner",
        "nav",
        "katex_dir",
    ]
    missing = [k for k in need if k not in data]
    if missing:
        raise SystemExit(f"{SETTINGS}: missing keys {missing}")
    return data


PHOTO = re.compile(r"<!--PHOTO:([a-z0-9-]+)-->")
GALLERY = re.compile(r"<!--GALLERY:([a-z0-9-]+)-->")
FIGURE = re.compile(r"<!--FIGURE:([a-z0-9-]+)-->")
VIDEO = re.compile(r"<!--VIDEO:([a-z0-9-]+)-->")
MATH = "<!--MATH-->"
TEAM = "<!--TEAM-->"
# Photo originals (HEIC, full-size JPEG) stay out of the site; only the
# clean web copies in img/photos/web/ are published (tools/prepare_photos.py).
ORIGINALS = re.compile(r"^img/photos/(?!web/)")
DRAFTBAR = '<div class="draftbar">DRAFT: this site is under team review.</div>'


def photo_html(name: str, slots: dict, root: str) -> str:
    """Return the markup of one photo slot (see website/images.json).

    A slot with a file gives an <img>. An empty slot gives a labelled
    placeholder that holds a review marker, so the site cannot deploy
    with a missing photo.
    """
    if name not in slots:
        raise SystemExit(f"images.json has no photo slot '{name}'")
    slot = slots[name]
    if not slot.get("file"):
        want = html.escape(slot.get("want", ""))
        return (
            f'<figure class="photo empty" data-slot="{name}"><div class="ph">'
            f'<b>PHOTO · {name}</b><span class="review">Photo needed: {want}</span></div></figure>'
        )
    load = "eager" if name == "hero" else "lazy"
    alt = html.escape(slot.get("alt", ""))
    cap = slot.get("credit", "")
    cap_html = f'<figcaption class="credit">{html.escape(cap)}</figcaption>' if cap else ""
    return (
        f'<figure class="photo" data-slot="{name}"><img src="{root}img/photos/web/{slot["file"]}" '
        f'alt="{alt}" loading="{load}" decoding="async">{cap_html}</figure>'
    )


def figure_html(name: str, slots: dict, root: str) -> str:
    """Return a captioned figure (a plot or diagram, shown whole, not cropped).

    A click opens the full-size image. An empty slot shows a placeholder
    with a review marker, like a photo slot.
    """
    if name not in slots:
        raise SystemExit(f"images.json has no figure slot '{name}'")
    slot = slots[name]
    cap = slot.get("caption", "")
    if not slot.get("file"):
        want = html.escape(slot.get("want", ""))
        return (
            f'<figure class="fig-card empty" data-slot="{name}"><div '
            f'class="ph"><b>FIGURE · {name}</b>'
            f'<span class="review">Figure needed: {want}</span></div></figure>'
        )
    src = f"{root}img/photos/web/{slot['file']}"
    alt = html.escape(slot.get("alt", ""))
    return (
        f'<figure class="fig-card" data-slot="{name}"><a '
        f'href="{src}" target="_blank" rel="noopener">'
        f'<img src="{src}" alt="{alt}" loading="lazy" decoding="async"></a>'
        f"<figcaption>{cap}</figcaption></figure>"
    )


def video_html(name: str, data: dict, root: str) -> str:
    """Return a video player (images.json ``videos``).

    A video is a file in src/video/ (``file``, MP4 H.264, with a
    ``poster`` photo slot) or a hosted video (``embed``: a full
    https://www.youtube-nocookie.com/embed/... or player.vimeo.com URL).
    With neither, a placeholder with a review marker.
    """
    videos = data.get("videos", {})
    if name not in videos:
        raise SystemExit(f"images.json has no video '{name}'")
    v = videos[name]
    title = html.escape(v.get("title", name))
    if v.get("embed"):
        return (
            f'<div class="video"><iframe src="{html.escape(v["embed"])}" '
            f'title="{title}" loading="lazy" '
            'allow="accelerometer; encrypted-media; gyroscope; picture-in-picture; fullscreen" '
            'referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe></div>'
        )
    if v.get("file"):
        poster = ""
        if v.get("poster") and data["slots"].get(v["poster"], {}).get("file"):
            poster = f' poster="{root}img/photos/web/{data["slots"][v["poster"]]["file"]}"'
        return (
            '<div class="video"><video controls preload="metadata" '
            f'playsinline{poster} title="{title}">'
            f'<source src="{root}video/{html.escape(v["file"])}" type="video/mp4"></video></div>'
        )
    want = html.escape(v.get("want", ""))
    return (
        f'<div class="video empty"><div class="ph"><b>VIDEO · {name}</b>'
        f'<span class="review">Video needed: {want}</span></div></div>'
    )


def team_html(team: dict, slots: dict, root: str) -> str:
    """Return the people cards of the About page (website/team.json).

    A person with no ``role`` gets a review marker, so the page cannot
    deploy until the team fills it in. A person with no photo gets their
    initials.
    """
    out = []
    for group in team["groups"]:
        cards = []
        for p in group["people"]:
            slot = slots.get(p.get("photo") or "", {})
            if slot.get("file"):
                pic = (
                    f'<img src="{root}img/photos/web/{slot["file"]}" '
                    f'alt="{html.escape(p["name"])}" '
                    'loading="lazy" decoding="async">'
                )
            else:
                initials = "".join(w[0] for w in p["name"].split()[:2])
                pic = f'<span class="initials" aria-hidden="true">{initials}</span>'
            # role "" = still to be filled in (review marker); role None = no role line.
            raw_role = p.get("role", "")
            if raw_role is None:
                role_html = ""
            elif raw_role:
                role_html = f'<span class="role">{html.escape(raw_role)}</span>'
            else:
                role_html = '<span class="role"><span class="review">Role needed</span></span>'
            duties = "".join(f"<li>{html.escape(d)}</li>" for d in p.get("duties", []))
            duties_html = f'<ul class="duties">{duties}</ul>' if duties else ""
            cls = "person" + (" wide" if duties else "")
            cards.append(
                f'<div class="{cls}"><div class="pic">{pic}</div>'
                f"<b>{html.escape(p['name'])}</b>{role_html}{duties_html}</div>"
            )
        out.append(
            f'<h3 class="group">{html.escape(group["name"])}</h3><div '
            f'class="people">{"".join(cards)}</div>'
        )
    return "".join(out)


def math_html(root: str) -> str:
    """KaTeX (served from the site, so equations work offline) for pages with math."""
    k = f"{root}vendor/katex/"
    # Delimiters: $$...$$ for display math, \(...\) for inline math. In the
    # JavaScript string a backslash is written twice.
    opts = (
        "{delimiters:[{left:'$$',right:'$$',display:true},"
        r"{left:'\\(',right:'\\)',display:false}],throwOnError:false}"
    )
    return (
        f'<link rel="stylesheet" href="{k}katex.min.css">\n'
        f'<script defer src="{k}katex.min.js"></script>\n'
        f'<script defer src="{k}auto-render.min.js" '
        f'onload="renderMathInElement(document.body,{opts})"></script>\n'
    )


def gallery_html(name: str, data: dict, root: str) -> str:
    """Return a captioned grid of photo slots (images.json ``galleries``)."""
    if name not in data.get("galleries", {}):
        raise SystemExit(f"images.json has no gallery '{name}'")
    items = []
    for slot in data["galleries"][name]:
        cap = html.escape(data["slots"][slot].get("caption", ""))
        items.append(
            f'<div class="g-item">{photo_html(slot, data["slots"], root)}<span>{cap}</span></div>'
        )
    return f'<div class="gallery" data-gallery="{name}">{"".join(items)}</div>'


def render_page(src: Path, src_root: Path, partials: dict[str, str], images: dict) -> str:
    """Fill the <!--HEAD-->, <!--TOP-->, <!--FOOT--> and <!--PHOTO:x--> markers.

    ROOT is the relative path back to the site root, so the site works
    from any folder (a local preview or the real domain). The draft bar
    is left as {{DRAFTBAR}}; build_pages fills it once it knows whether
    any page still has a review marker.
    """
    depth = len(src.relative_to(src_root).parts) - 1
    root = "../" * depth
    section = src.relative_to(src_root).parts[0] if depth else ""
    text = src.read_text(encoding="utf-8")
    for marker, key in (("<!--HEAD-->", "head"), ("<!--TOP-->", "top"), ("<!--FOOT-->", "foot")):
        text = text.replace(marker, partials[key])
    text = GALLERY.sub(lambda m: gallery_html(m.group(1), images, root), text)
    text = FIGURE.sub(lambda m: figure_html(m.group(1), images["slots"], root), text)
    text = VIDEO.sub(lambda m: video_html(m.group(1), images, root), text)
    text = text.replace(MATH, math_html(root))
    if TEAM in text:
        team = json.loads((REPO / "website" / "team.json").read_text(encoding="utf-8"))
        text = text.replace(TEAM, team_html(team, images["slots"], root))
    text = PHOTO.sub(lambda m: photo_html(m.group(1), images["slots"], root), text)
    text = text.replace("{{ROOT}}", root)
    for name in ("about", "publications", "course", "sim"):
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
    images = json.loads((REPO / cfg["images"]).read_text(encoding="utf-8"))
    pages = [
        p
        for p in src_root.rglob("*")
        if p.is_file()
        and "_partials" not in p.parts
        and not ORIGINALS.match(p.relative_to(src_root).as_posix())
    ]
    rendered: dict[Path, str] = {}
    for src in pages:
        dest = out / src.relative_to(src_root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix == ".html":
            rendered[dest] = render_page(src, src_root, partials, images)
        else:
            shutil.copy2(src, dest)
    count = sum(len(REVIEW.findall(t)) for t in rendered.values())
    for dest, text in rendered.items():
        dest.write_text(text.replace("{{DRAFTBAR}}", DRAFTBAR if count else ""), encoding="utf-8")
    return count


def inject_nav(page: str, cfg: dict, here: str, pos: str) -> str:
    """Add the site menu (☰) to a page that keeps its own layout.

    The course and the planner have their own top bars, so they get a
    floating menu button instead of the site's top bar (nav/site_nav.js).
    """
    nav = cfg["nav"]
    data = {"root": "../", "here": here, "pos": pos, "logo": nav["logo"], "links": nav["links"]}
    snippet = (
        f"<script>window.HERON_NAV = {json.dumps(data, ensure_ascii=False)};</script>\n"
        '<script src="../site_nav.js" defer></script>\n'
    )
    if "</body>" not in page:
        raise SystemExit(f"the {here} page has no </body>; cannot add the site menu")
    return page.replace("</body>", snippet + "</body>", 1)


def build_course(cfg: dict, out: Path) -> None:
    """Build the course straight into /course/index.html."""
    dest = out / "course" / "index.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, str(REPO / cfg["course_build"]), "--out", str(dest)], check=True
    )
    page = dest.read_text(encoding="utf-8")
    dest.write_text(inject_nav(page, cfg, "course", "top-right"), encoding="utf-8")


def _git(*args: str) -> str:
    """Run git in the repository and return its output."""
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout


def planner_source(pcfg: dict, cache: Path) -> tuple[Path, str]:
    """Extract the planner from its git ref into ``cache``. Return (folder, description)."""
    ref = pcfg["git_ref"]
    commit = _git("rev-parse", "--verify", ref + "^{commit}").strip()
    src = cache / "planner_src"
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    tar = cache / "planner.tar"
    with open(tar, "wb") as fh:
        subprocess.run(
            ["git", "archive", "--format=tar", commit, "Specular_Planner", "HERON_Payload"],
            cwd=REPO,
            check=True,
            stdout=fh,
        )
    shutil.unpack_archive(str(tar), str(src))
    tar.unlink()
    date = _git("show", "-s", "--format=%cs", commit).strip()
    return src, f"{ref} @ {commit[:9]} ({date})"


def planner_deps(pcfg: dict, cache: Path, given: Path | None) -> Path:
    """Folder with the pure-Python packages: given, or installed with pip."""
    if given:
        return given
    target = cache / "pydeps"
    if not target.exists():
        req = REPO / pcfg["requirements"]
        cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--target", str(target)]
        subprocess.run([*cmd, "-r", str(req)], check=True)
    return target


def _add_tree(zf, folder: Path, arc: str) -> None:
    """Add a folder to the zip, without caches and binary modules."""
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder).as_posix()
        if path.is_file() and not SKIP.search(rel):
            zf.write(path, f"{arc}/{rel}")


def build_planner(cfg: dict, out: Path, deps_dir: Path | None) -> int:
    """Build /sim/: the planner page, the worker, and the Python bundle.

    Return the number of review markers on the page (Jack's approval).
    """
    import zipfile

    pcfg = cfg["planner"]
    cache = REPO / pcfg["cache_dir"]
    cache.mkdir(parents=True, exist_ok=True)
    src, source = planner_source(pcfg, cache)
    deps = planner_deps(pcfg, cache, deps_dir)
    dest = out / "sim"
    shutil.copytree(src / "Specular_Planner" / "static", dest / "static")
    # The Python bundle: planner code and data, the payload package, deps.
    with zipfile.ZipFile(dest / "planner_bundle.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in PLANNER_CODE:
            path = src / rel
            if path.is_dir():
                _add_tree(zf, path, rel)
            else:
                zf.write(path, rel)
        _add_tree(zf, src / PAYLOAD_CODE, PAYLOAD_CODE)
        for name in pcfg["dep_packages"]:
            if not (deps / name).is_dir():
                raise SystemExit(f"package {name} is missing from {deps}")
            _add_tree(zf, deps / name, f"deps/{name}")
            # Package metadata: Flask's test client asks for werkzeug's version.
            for info in deps.glob(f"{name}-*.dist-info"):
                for meta in ("METADATA", "RECORD", "top_level.txt", "LICENSE.txt", "LICENSE"):
                    if (info / meta).is_file():
                        zf.write(info / meta, f"deps/{info.name}/{meta}")
        zf.write(REPO / pcfg["dir"] / "web_backend.py", "web_backend.py")
        zf.writestr("SOURCE.txt", source + "\n")
    for name in ("worker.js", "bridge.js", "planner_web.css"):
        shutil.copy2(REPO / pcfg["dir"] / name, dest / name)
    worker_cfg = {
        "pyodide_url": pcfg["pyodide_url"],
        "packages": pcfg["packages"],
        "bundle": "planner_bundle.zip",
        "root": "/home/pyodide/planner",
    }
    (dest / "planner_config.json").write_text(json.dumps(worker_cfg, indent=2), encoding="utf-8")
    # The page: Jack's index.html with relative /static/ links, plus the bridge.
    page = (src / "Specular_Planner" / "static" / "index.html").read_text(encoding="utf-8")
    if "</head>" not in page or not re.search(r"<body[^>]*>", page):
        raise SystemExit("the planner page has no </head> or <body>; cannot add the web bridge")
    page = page.replace('"/static/', '"static/')
    approval = (
        ""
        if pcfg.get("approved_by_author")
        else '<span class="review">Jack Abrams approves publishing his planner here; '
        "then set planner.approved_by_author in site.json.</span>"
    )
    banner = (
        '<template id="web-banner-tpl"><div id="web-banner">'
        '<span class="tag">WEB DEMO</span>'
        "<span>Jack Abrams' mission planner, running in your browser. "
        "Plan and Simulate are the real code; Fly needs a real aircraft.</span>"
        '<a href="../">← herongnss.org</a><span class="src"></span>'
        f"{approval}"
        '<button type="button" aria-label="Hide" onclick="this.parentNode.remove()">×</button>'
        "</div></template>\n"
    )
    inject = '<link rel="stylesheet" href="planner_web.css">\n<script src="bridge.js"></script>\n'
    page = page.replace("</head>", inject + "</head>", 1)
    page = re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + "\n" + banner, page, count=1)
    page = re.sub(r"<title>.*?</title>", f"<title>{pcfg['title']}</title>", page, count=1)
    page = inject_nav(page, cfg, "sim", "bottom-left")
    (dest / "index.html").write_text(page, encoding="utf-8")
    size = (dest / "planner_bundle.zip").stat().st_size / 1e6
    print(f"planner: {source}; bundle {size:.1f} MB")
    return len(REVIEW.findall(page))


def build(final: bool, deps_dir: Path | None = None) -> tuple[Path, int]:
    """Build the whole site. Return the output folder and the marker count."""
    cfg = load_settings()
    out = REPO / cfg["out_dir"]
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    reviews = build_pages(cfg, out)
    build_course(cfg, out)
    reviews += build_planner(cfg, out, deps_dir)
    # KaTeX for the pages with equations (<!--MATH-->), from the course's copy.
    shutil.copytree(REPO / cfg["katex_dir"], out / "vendor" / "katex")
    shutil.copy2(REPO / cfg["nav"]["script"], out / "site_nav.js")
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
    ap.add_argument(
        "--planner-deps", type=Path, help="site-packages with the planner's pure-Python deps"
    )
    args = ap.parse_args()
    out, _ = build(args.final, args.planner_deps)
    if args.serve:
        serve(out, args.port)


if __name__ == "__main__":
    main()
