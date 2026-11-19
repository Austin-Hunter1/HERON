# Editing herongnss.org — a guide for the HERON team

This guide tells you how to change the HERON website. You can do most
changes in your web browser on GitHub. You do not need to install anything
for text changes.

- **Live site:** https://herongnss.org
- **Repository:** https://github.com/Austin-Hunter1/HERON
- **Website files:** the `website/` folder (and `docs/teaching/` for the GNSS course)

> **The short version.** Edit a file in `website/` on GitHub, commit it to
> `main`, and the site updates by itself in about one minute. If the site
> does not update, open the **Actions** tab and read the newest
> "Publish herongnss.org" run (see [Check a deploy](#6-check-a-deploy)).

---

## Contents

1. [How the site works](#1-how-the-site-works)
2. [Where each page lives](#2-where-each-page-lives)
3. [Edit a file on GitHub (step by step)](#3-edit-a-file-on-github-step-by-step)
4. [Common tasks](#4-common-tasks)
5. [Draft notes: hold back unfinished text](#5-draft-notes-hold-back-unfinished-text)
6. [Check a deploy](#6-check-a-deploy)
7. [Preview on your own computer (optional)](#7-preview-on-your-own-computer-optional)
8. [Rules](#8-rules)
9. [Troubleshooting](#9-troubleshooting)
10. [Who owns what](#10-who-owns-what)

---

## 1. How the site works

```
 you edit a file on GitHub ──► commit to main ──► GitHub Actions builds the site ──► herongnss.org
                                                  (website/build_site.py)          (GitHub Pages)
```

- The site is **static**: plain HTML pages, pictures and videos. There is no
  server and no database.
- `website/build_site.py` assembles the pages. It adds the shared header and
  footer, puts the photos and figures into the pages, builds the GNSS course,
  and packs the mission planner demo.
- The workflow `.github/workflows/website.yml` runs that build:
  - on every push to `main` that changes `website/`, `docs/teaching/` or the
    workflow file;
  - once a day (09:17 UTC), so the planner demo follows Jack's planner branch;
  - when you press **Run workflow** on the Actions tab.
- The site goes live only if the build has **no draft notes** left (see
  [section 5](#5-draft-notes-hold-back-unfinished-text)). If a build fails,
  the live site keeps its last good version.

---

## 2. Where each page lives

| Page | Address | File to edit |
|---|---|---|
| Home | `/` | `website/src/index.html` |
| About (team, ConOps, SURGE heritage) | `/about/` | `website/src/about/index.html` |
| Publications + results | `/publications/` | `website/src/publications/index.html` |
| GNSS signal course | `/course/` | `docs/teaching/src/` (see [4.10](#410-the-gnss-course)) |
| Mission planner demo | `/sim/` | Comes from Jack's planner code (see [4.11](#411-the-mission-planner-demo)) |

Shared parts and settings:

| What | File |
|---|---|
| Top bar (logo and menu) on Home, About, Publications | `website/src/_partials/top.html` |
| Footer | `website/src/_partials/foot.html` |
| Browser-tab icon and fonts | `website/src/_partials/head.html` |
| Colours, spacing, layout | `website/src/site.css` |
| Photos, figures and videos (the list) | `website/images.json` |
| Team cards on the About page | `website/team.json` |
| Menu (☰) on the course and planner pages; planner settings | `website/site.json` |
| Web copies of photos and figures | `website/src/img/photos/web/` |
| Videos | `website/src/video/` |
| Logo | `website/src/img/heron-logo-white-sky.png` |

**Markers in the pages.** The pages contain comments like
`<!--PHOTO:drone-ground-->`. The build replaces each one:

| Marker | Becomes |
|---|---|
| `<!--PHOTO:name-->` | the photo `name` from `images.json`, cropped to fill its box |
| `<!--FIGURE:name-->` | the figure `name` from `images.json`, shown whole, with its caption; a click opens it full size |
| `<!--VIDEO:name-->` | the video `name` from `images.json` |
| `<!--GALLERY:name-->` | a grid of photos (the list `galleries.name` in `images.json`) |
| `<!--TEAM-->` | the team cards from `team.json` |
| `<!--MATH-->` (in `<head>`) | turns on equations for that page |
| `<!--HEAD-->`, `<!--TOP-->`, `<!--FOOT-->` | the shared parts in `_partials/` |

Do not delete a marker unless you want to remove that item.

---

## 3. Edit a file on GitHub (step by step)

1. Open https://github.com/Austin-Hunter1/HERON and sign in. (You need write
   access. Ask Austin if you do not have it.)
2. Click into the folders to the file, for example `website` → `src` →
   `index.html`.
3. Click the **pencil** icon ("Edit this file") at the top right of the file.
4. Make the change. Use **Preview** to see the change in the text.
   (GitHub's preview does not show the finished web page.)
5. Click **Commit changes…**.
6. Write a short message that says what you changed, for example
   "Update the SURGE description".
7. Choose one:
   - **Commit directly to the `main` branch** — for small text fixes. The
     site updates in about one minute.
   - **Create a new branch and start a pull request** — for larger changes,
     or when you want someone to check them first. The site updates when the
     pull request is merged into `main`.
8. Wait one or two minutes, then open the page and **hard refresh**
   (Ctrl + F5 on Windows, Cmd + Shift + R on a Mac).

To **add a new file** (for example a figure): open the folder, then
**Add file** → **Upload files**, drag the file in, and commit.

---

## 4. Common tasks

### 4.1 Change text on a page

Open the page's file (table in [section 2](#2-where-each-page-lives)) and
change the words between the tags. Leave the tags alone.

```html
<p class="lede">We map surface water from a drone, using GPS, Galileo and BeiDou signals that reflect off Inland Water Bodies.</p>
                ↑ change only this text                                                                              ↑
```

Useful tags:

| You want | Write |
|---|---|
| A paragraph | `<p>Text.</p>` |
| A section heading | `<h2>Title</h2>`; a smaller one: `<h3>Title</h3>` |
| Bold, italic | `<b>bold</b>`, `<em>italic</em>` |
| A link | `<a href="https://example.org">link text</a>` |
| A link to another page of the site | `<a href="publications/">…</a>` from Home, `<a href="../publications/">…</a>` from About |
| A bullet list | `<ul><li>One</li><li>Two</li></ul>` |
| The characters `&`, `<`, `>` in text | `&amp;`, `&lt;`, `&gt;` |

Each page is made of `<section class="band" id="...">` blocks. To move a
section, cut the whole block (from `<section` to its `</section>`) and paste
it in the new place. The small buttons under the top of Home and About link
to these blocks by their `id`.

### 4.2 Add or change a photo

**Important:** phone photos contain your GPS position and the phone's
serial number. Do not put an original phone photo on the site. Use one of
the two ways below; both remove that data.

**Way A — on your computer (recommended; it also reads iPhone HEIC files):**

1. Put the original in `website/src/img/photos/` (git ignores the originals;
   they never leave your computer).
2. In `website/images.json`, add or change a slot:

   ```json
   "drone-ground": {
     "source": "website/src/img/photos/drone.heic",
     "file": "drone.jpg",
     "alt": "Two team members prepare the HERON hexacopter on the launch mat",
     "credit": "",
     "want": "The drone on the ground with the payload fitted.",
     "max_px": 2400
   }
   ```

   | Field | Meaning |
   |---|---|
   | `source` | the original file (path from the repository folder) |
   | `file` | the name of the web copy; `.jpg` for photos, `.webp` for plots and diagrams |
   | `alt` | a short description for screen readers — always fill it in |
   | `caption` | text under a figure (`<!--FIGURE:...-->` only) |
   | `credit` | small text on the photo, for example a photographer's name |
   | `max_px` | longest side of the web copy (optional; default 2400) |
   | `crop` | optional `[left, top, right, bottom]` as fractions, to cut one person out of a group photo, for example `[0.27, 0.34, 0.45, 0.58]` |

3. Run, in the repository folder:

   ```bash
   python website/tools/prepare_photos.py
   ```

   It writes a clean web copy to `website/src/img/photos/web/` (turned
   upright, resized, all metadata removed).
4. Put the marker where the photo should go, for example
   `<!--PHOTO:drone-ground-->`, and commit `images.json`, the page, and the
   new file in `website/src/img/photos/web/`.

**Way B — only on GitHub (for a JPG you already cleaned):**

1. Remove the location data first: on Windows, right-click the file →
   **Properties** → **Details** → **Remove Properties and Personal
   Information**; on an iPhone, turn off **Location** in the share options.
   Make it under about 2400 px wide.
2. Upload it to `website/src/img/photos/web/`.
3. In `images.json`, add a slot with `"file": "your-file.jpg"` (you can
   leave `source` empty), and put the marker in the page.

**To change a photo that is already on the site**, keep the slot name and
change its `file` (and `source`). **To remove a photo**, delete its marker
from the page.

### 4.3 Add a figure (plot or diagram)

Same as a photo, but:

- use a `.webp` file name (it keeps thin lines and text sharp);
- add a `caption`;
- use `<!--FIGURE:name-->` in the page.

`source` can be a file outside the repository, for example a processing
report: `"source": "D:/01_Data/outputs/reports/20261116_HERON_Payload/..."`.

Two figures side by side:

```html
<div class="figs2">
  <!--FIGURE:res-g09-phase-->
  <!--FIGURE:res-g09-ffz-->
</div>
```

### 4.4 Change a team member or a role

Edit `website/team.json`:

```json
{
  "name": "Grace Halbleib",
  "role": "Software & Flight Compliance Lead",
  "photo": "team-grace",
  "duties": [
    "Manage FAA and CU Flight Ops authorizations and regulatory compliance",
    "..."
  ]
}
```

- `photo` is a slot name in `images.json`. For a portrait, use a `crop` so
  the face fills the round frame.
- `"role": null` shows no role (used for the advisors). `"role": ""` shows a
  "Role needed" draft note.
- `duties` is optional.

The group photo at the top of the team section is the slot `team-group`.

### 4.5 Add a publication or a reference

Edit `website/src/publications/index.html`. Each entry is one `<li>` in the
list under **Publications** or **References**. Use APA 7 style, and keep the
references in alphabetical order:

```html
<li>Author, A. A., &amp; Author, B. B. (2026). Title of the article. <i>Journal Name, 12</i>(3), 45–67. <a href="https://doi.org/10.xxxx/xxxxx">https://doi.org/10.xxxx/xxxxx</a></li>
```

If you cite a new paper on the Home page (the science section), add it to the
reference list too.

### 4.6 Change a video

Videos are listed in `images.json` under `videos`:

```json
"mission": {
  "title": "The 16 Nov 2026 flight in the HERON mission planner",
  "file": "heron-mission-planner-flight.mp4",
  "embed": "",
  "poster": ""
}
```

- **An MP4 file:** H.264, under 100 MB (GitHub's limit per file). Upload it
  to `website/src/video/` and set `file`.
- **YouTube or Vimeo (better for long videos):** leave `file` empty and set
  `embed` to the player address, for example
  `https://www.youtube-nocookie.com/embed/VIDEO_ID` or
  `https://player.vimeo.com/video/VIDEO_ID`.
- `poster` (optional) is a photo slot shown before the video plays.

Large videos make the repository bigger for everyone, so prefer YouTube or
Vimeo for new ones.

### 4.7 Write equations

A page with `<!--MATH-->` in its `<head>` shows LaTeX equations (KaTeX):

- inline: `\( 2h\sin e \)`
- on its own line: `$$ \Delta\rho = \rho_R - \rho_D \approx 2h\sin e $$`

In an HTML file, write `\lt` instead of `<` inside an equation (for example
`|x| \lt 1`), and `&amp;` for `&`. A wrong equation shows in red on the
page; the rest of the page still works.

The science section on Home uses fold-out cards:

```html
<details class="need-card">
  <summary><span class="nk">05</span><span><b>Question</b><em>Short subtitle</em></span></summary>
  <div class="nb">
    <p>Text.</p>
    $$ equation $$
  </div>
</details>
```

Add `open` (`<details class="need-card" open>`) to show a card unfolded.

### 4.8 Change the menu

- **Top bar** (Home, About, Publications): `website/src/_partials/top.html`.
  Add a link line like the others.
- **☰ menu** on the course and the planner: `nav.links` in
  `website/site.json`.

A new page: copy `website/src/publications/index.html` to a new folder, for
example `website/src/outreach/index.html`, change its title and content, and
add it to both menus. In `top.html`, the `{{CUR_name}}` part highlights the
current page; for a new page also add its folder name to the list
`("about", "publications", "course", "sim")` in `website/build_site.py`.

### 4.9 Change the logo

Replace `website/src/img/heron-logo-white-sky.png`. If browsers keep showing
the old logo, give the new file a **new name** and change the name in:
`_partials/top.html`, `_partials/head.html`, `src/index.html`,
`site.json` (`nav.logo`), and `planner/bridge.js`.

### 4.10 The GNSS course

- Content: `docs/teaching/src/sections/sNN.html` (text and figures) and
  `docs/teaching/src/js/sNN.js` (the interactive figures), one pair per part.
- The list and order of parts: `PARTS` in `docs/teaching/build.py`.
- On phones the course shows a shorter layout (`src/js/phone.js`,
  `src/css/phone.css`): each notes block shows its heading, first paragraph,
  equations and controls, and folds the rest behind "Read more". So put the
  key sentence first.
- The site build rebuilds the course. To also update the stand-alone file
  `docs/teaching/gnss_course.html`, run `python docs/teaching/build.py`.

See `docs/teaching/README.md` for details.

### 4.11 The mission planner demo

The `/sim/` page is Jack Abrams' real planner, running in the browser. It is
built from the git branch in `website/site.json` → `planner.git_ref`
(now `origin/mission-planner-v2`). Change the planner itself on that branch,
not in `website/`. **When the branch is merged or deleted, set `git_ref` to
`main`**, or the daily build fails. Before each deploy the workflow runs a
smoke test (`website/planner/smoke_test.py`); if it fails after a planner
change, the live site keeps the last good version. Details:
`website/README.md`.

---

## 5. Draft notes: hold back unfinished text

A **draft note** is a yellow "REVIEW:" label on the page:

```html
<span class="review">Confirm the flight altitude.</span>
```

- While **any** draft note is left anywhere on the site, the site **does not
  deploy**. The Actions run says "Not deployed: N review marker(s)". The live
  site keeps its last version. This stops unchecked text from going live.
- An empty photo or figure slot, a missing video, and a team member with
  `"role": ""` also count as draft notes.
- To finish: correct the text and delete the `<span class="review">…</span>`.
- To hide something unfinished without blocking the site, put it in an HTML
  comment instead, and do **not** write the words `class="review"` inside
  the comment (the build would count them):

  ```html
  <!-- TODO (team): add the sponsors here. -->
  ```

Open TODOs on the site now: the SURGE team photo and the sponsors (About
page), and the URSI meeting details (Publications page). Search the files
for `TODO (team)`.

---

## 6. Check a deploy

1. Open the **Actions** tab of the repository.
2. Click the newest **Publish herongnss.org** run.
   - Green **build** and green **deploy**: the site is updated. Hard refresh
     the page (Ctrl + F5).
   - Green **build**, **deploy** skipped: a draft note is left. The summary
     says how many.
   - Red **build**: click it and read the error (see
     [Troubleshooting](#9-troubleshooting)).
3. To run it by hand: **Run workflow** → branch `main` → **Run workflow**.

---

## 7. Preview on your own computer (optional)

You need Python 3.11 or newer, and git.

```bash
git clone https://github.com/Austin-Hunter1/HERON.git
cd HERON
python website/build_site.py --serve
```

Then open http://localhost:8766/. The build puts the result in
`website/_site/` (not committed). Notes:

- The first build downloads a few Python packages for the planner demo
  (internet needed). To skip that, point it at an environment that has them:
  `python website/build_site.py --planner-deps <path to site-packages>`.
- `python website/build_site.py --final` fails if a draft note is left.
- `prepare_photos.py` needs Pillow: `pip install pillow`.

---

## 8. Rules

- **No secrets.** No passwords, API keys, or private data in the repository.
  It is public.
- **People.** Show a person only with their permission. Use first names in
  captions unless the person agrees to more.
- **Photos.** Always remove the location data (see [4.2](#42-add-or-change-a-photo)).
- **Facts.** Results and numbers must come from the team's processed data;
  mark preliminary results as preliminary.
- **Citations.** APA 7, with a DOI link when there is one.
- **File size.** Photos under about 1 MB each after `prepare_photos.py`;
  videos under 100 MB, or on YouTube/Vimeo.
- **Bigger changes** (new pages, layout, the build): use a pull request so
  someone can check them first.

---

## 9. Troubleshooting

| Problem | Cause and fix |
|---|---|
| The page did not change | Wait two minutes, then hard refresh (Ctrl + F5). Check the Actions run. |
| "Not deployed: N review marker(s)" | A draft note is left. Search the `website/src` files for `class="review"`, and check `team.json` for `"role": ""`. |
| Build error: `images.json has no photo slot 'x'` | A page has `<!--PHOTO:x-->` (or FIGURE) but `images.json` has no slot `x`. Fix the name or add the slot. |
| Build error mentioning `images.json` or `team.json` | The JSON is broken: usually a missing comma, or a comma after the last item. Paste the file into https://jsonlint.com to find the line. |
| A photo shows as a broken image | The `file` name in `images.json` does not match the file in `website/src/img/photos/web/` (names are case-sensitive). |
| An equation shows in red | A LaTeX error. Check the brackets; write `\lt` for `<`. |
| "Smoke-test the planner backend" failed | A change on the planner branch broke the demo. The live site keeps the last version. Tell Austin or Jack. |
| The old logo or photo still shows | Your browser keeps old copies. Hard refresh, or give the file a new name. |

---

## 10. Who owns what

| Item | Owner |
|---|---|
| The repository and its GitHub Pages settings | Austin (`Austin-Hunter1`) |
| The domain `herongnss.org` and its DNS records | Austin (registrar account) |
| The mission planner demo (`/sim/`) | Jack Abrams (planner code) |
| Decisions about the website | `docs/DECISIONS.md`, entries D-116 to D-122 |

For how the build works inside, see `website/README.md`.
