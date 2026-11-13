# herongnss.org

The public HERON website (decisions D-102 to D-105 in `docs/DECISIONS.md`).

| Path | What | Source |
|---|---|---|
| `/` and `/about/` | Home and About HERON | `src/` (hand-written HTML, shared parts in `src/_partials/`) |
| `/course/` | The GNSS signal course | `docs/teaching/` (built by `docs/teaching/build.py`) |
| `/sim/` | The real ground station page with a simulated payload | `HERON_DEPLOY_SOFTWARE/.../heron_base/web/index.html` + `sim/` |

## Build and preview

Standard library only. From the repository root:

```bash
python website/build_site.py --serve
```

Then open http://localhost:8766/. The output is in `website/_site/` (not committed).

## Publishing

`.github/workflows/website.yml` builds the site on every push to `main` that
touches the site, the course or the dashboard page. It deploys to GitHub Pages
only when the build has **no review markers**.

A review marker is `<span class="review">…</span>` around a claim the team
must check. It shows as a yellow "REVIEW" note in a local preview. To approve a
claim, correct the text and remove the span. `python website/build_site.py
--final` fails while any marker is left.

One-time setup by the repository owner:

1. Settings → Pages → Source: **GitHub Actions**.
2. Verify the domain in the GitHub account settings (Pages → verified domains),
   then set the custom domain to `herongnss.org`.
3. At the registrar, add the GitHub Pages DNS records (A and AAAA for the apex,
   CNAME `www` → `austin-hunter1.github.io`).

## The simulation

`sim/heron_sim.js` is a JavaScript port of the payload supervisor, the
recording state machine, the fake capture backend, the ground `LinkClient` and
the dashboard snapshot. It runs entirely in the browser; nothing can reach real
hardware. `sim/sim_shim.js` replaces `fetch` for `/api/state` and `/api/command`
so the unchanged dashboard page talks to it, and adds the scenario panel.

- `sim/sim_config.json` and `sim/snapshot_template.json` are made from the real
  Python code by `tools/make_sim_data.py`. Do not edit them by hand.
- `sim/scenario.json` holds simulation-only values (disk size, link latency,
  CPU and temperature ranges, the simulated GNSS fix).
- `Base_Software/tests/test_web_sim_data.py` fails when the JSON files are out of
  date. Then run, in `HERON_DEPLOY_SOFTWARE/`:

  ```bash
  uv run python ../website/tools/make_sim_data.py
  ```

  If a rule in the Python code changed, change `sim/heron_sim.js` in the same
  commit. The tests check the values and the snapshot layout, not the rules.
