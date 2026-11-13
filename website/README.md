# herongnss.org

The public HERON website (decisions D-102 to D-107 in `docs/DECISIONS.md`).

| Path | What | Source |
|---|---|---|
| `/` and `/about/` | Home and About HERON | `src/` (hand-written HTML, shared parts in `src/_partials/`, photos from `images.json`) |
| `/course/` | The GNSS signal course | `docs/teaching/` (built by `docs/teaching/build.py`) |
| `/sim/` | Jack Abrams' mission planner, running in the browser | `Specular_Planner/` + `HERON_Payload/` from a git ref, and `planner/` |

The course and the planner keep their own layouts, so they get a floating
menu button (☰) instead of the site's top bar: `nav/site_nav.js`, with the
links from `site.json` → `nav`. The build adds it to both pages.

## Build and preview

From the repository root:

```bash
python website/build_site.py --serve
```

Then open http://localhost:8766/. The output is in `website/_site/` (not committed).

The planner needs a few pure-Python packages (`planner/requirements-pyodide.txt`).
Without `--planner-deps`, the build installs them with pip into `website/_cache/`.
To use packages you already have (for example the planner's own venv):

```bash
python website/build_site.py --planner-deps Specular_Planner/.venv/Lib/site-packages
```

## Publishing

`.github/workflows/website.yml` builds the site on every push to `main` that
touches the site or the course, once a day (to follow the planner branch), and
on demand. It smoke-tests the planner backend, then deploys to GitHub Pages
only when the build has **no review markers**.

A review marker is `<span class="review">…</span>` around a claim the team
must check. It shows as a yellow "REVIEW" note in a local preview. To approve a
claim, correct the text and remove the span. An empty photo slot and the
planner's author approval also count as review markers.
`python website/build_site.py --final` fails while any marker is left.

One-time setup by the repository owner:

1. Settings → Pages → Source: **GitHub Actions**.
2. Verify the domain in the GitHub account settings (Pages → verified domains),
   then set the custom domain to `herongnss.org`.
3. At the registrar, add the GitHub Pages DNS records (A and AAAA for the apex,
   CNAME `www` → `austin-hunter1.github.io`).

## Photos

`images.json` lists the photo slots and the `team` gallery. Each slot names
its original in `source` and its web copy in `file`.

1. Put the original in `src/img/photos/` (HEIC, JPEG or PNG are fine).
2. Point a slot's `source` at it, and set `file`, `alt` and, for the gallery,
   `caption`.
3. Run `python website/tools/prepare_photos.py`. It writes a clean copy to
   `src/img/photos/web/`: upright, resized, and **without GPS or other
   metadata**. On Windows it reads HEIC with the built-in Windows decoder.

Only `src/img/photos/web/` is committed and published; the originals are
ignored by git. A slot with no photo shows a labelled placeholder and blocks
the deploy.

## The mission planner demo (`/sim/`)

The page is Jack Abrams' planner page (`Specular_Planner/static/index.html`),
unchanged except for a few lines in `<head>`. Its Python backend, `app.py` with
`engine/` and `heron_payload`, **runs in the browser** with Pyodide in a Web
Worker. So planning, the launch checks, the demo aircraft and the simulated
payload are Jack's real code, not a copy.

- `planner/worker.js` loads Pyodide (version in `site.json`), numpy and shapely,
  and the planner bundle, then answers the page's `/api/...` requests.
- `planner/bridge.js` sends those requests to the worker, saves exported files,
  shows the loading screen and the "web demo" tag, and selects the quick-preview
  simulator (SITL and the dry run need the desktop app).
- `planner/web_backend.py` patches what a browser cannot do: threads (poll loops
  run every 100 ms; a flight operation runs at once on a virtual clock, without
  the wait for the planned takeoff time), plain locks, and the clock.
- `planner/smoke_test.py` runs the same patches under CPython: plan, connect,
  upload, launch, fly. CI runs it before a deploy.

The planner code comes from `site.json` → `planner.git_ref`
(`origin/mission-planner-v2` now; set it to `main` after the merge). The tag on
the page shows the commit. `planner.approved_by_author` stays `false` until Jack
agrees to publish; until then the page has a review marker.
