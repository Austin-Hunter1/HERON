/* phone.js - Phone layout of the GNSS course.
 *
 * Why: on a phone the course is one very long page (about 45 000 px)
 * with 25 000 characters of notes, and the parts list sits under the
 * introduction. On a narrow screen (see PHONE_QUERY) this script:
 *
 * 1. Adds a top bar with a parts menu and the name of the current part.
 * 2. Adds a bottom bar that jumps to the previous or next part.
 * 3. Shortens the notes: each notes block keeps its heading, its first
 *    paragraph, its equations and its controls. The rest (more text, the
 *    "where" lines under equations, long remarks) folds behind a
 *    "Read more" button. The menu has a switch to show all text.
 *
 * It adds the class "phone" to <html>; phone.css styles everything under
 * that class, so the desktop and present-mode layouts do not change.
 * It reads the page as it is (parts, headings, notes); it needs no change
 * to the sections.
 */
(function () {
  "use strict";
  const PHONE_QUERY = "(max-width: 760px)";
  // Notes children that always stay visible on a phone.
  const KEEP = "h3, .eq, .controls, .readouts, .btnrow, .steps, .stepbody, .viz, .fig, .figrow, canvas, table, .tablewrap";
  const mq = window.matchMedia(PHONE_QUERY);
  let built = false;

  function partsList() {
    return [...document.querySelectorAll("section.part")].map((s) => ({
      el: s,
      id: s.id,
      num: (s.querySelector(".part-h .num") || {}).textContent || "",
      title: (s.querySelector(".part-h h2") || {}).textContent || s.id,
      min: s.dataset.min || "",
    }));
  }

  /** Fold everything after the first paragraph of one notes block. */
  function shorten(box) {
    let firstP = false, folded = 0;
    for (const c of [...box.children]) {
      if (c.matches(".pmore-btn")) continue;
      if (c.matches(KEEP)) {
        if (c.matches(".stepbody")) shorten(c);
        // An equation keeps its formula; its "where" line folds.
        c.querySelectorAll(":scope > .where").forEach((w) => { w.classList.add("pmore"); folded++; });
        continue;
      }
      if (c.tagName === "P" && !firstP && !c.matches(".tech, .hint")) { firstP = true; continue; }
      c.classList.add("pmore"); folded++;
    }
    if (folded && !box.querySelector(":scope > .pmore-btn")) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "pmore-btn"; b.textContent = "Read more";
      b.addEventListener("click", () => {
        const open = box.classList.toggle("pmore-open");
        b.textContent = open ? "Show less" : "Read more";
      });
      box.append(b);
    }
  }

  function build() {
    if (built) return;
    built = true;
    const parts = partsList();
    document.querySelectorAll(".notes").forEach(shorten);
    // Long remarks directly in a panel (for example under HERON figures).
    document.querySelectorAll(".panel").forEach((p) => {
      const loose = [...p.children].filter((c) => c.matches("p.tech, p.hint"));
      if (loose.length) {
        const wrap = document.createElement("div");
        wrap.className = "notes panel-notes";
        loose[0].before(wrap); loose.forEach((c) => wrap.append(c));
        shorten(wrap);
      }
    });

    // Top bar: parts menu button and the current part.
    const bar = document.createElement("div");
    bar.id = "pbar";
    bar.innerHTML = '<button type="button" class="pmenu" aria-label="Course parts" aria-expanded="false">' +
      '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h10"/></svg>' +
      '<span>Parts</span></button><a class="pcur" href="#top">GNSS course</a>';
    document.body.prepend(bar);

    // Parts sheet.
    const sheet = document.createElement("div");
    sheet.id = "psheet"; sheet.hidden = true;
    sheet.innerHTML = '<div class="psheet-in"><div class="psheet-h"><b>HERON GNSS course</b><button type="button" class="pclose" aria-label="Close">×</button></div>' +
      '<ol>' + parts.map((p) => `<li><a href="#${p.id}"><span class="pn">${p.num}</span><span>${p.title}</span><span class="pm">${p.min}′</span></a></li>`).join("") + '</ol>' +
      '<label class="pall"><input type="checkbox"> Show all text</label></div>';
    document.body.append(sheet);
    const menuBtn = bar.querySelector(".pmenu");
    const setSheet = (open) => { sheet.hidden = !open; menuBtn.setAttribute("aria-expanded", String(open)); document.documentElement.classList.toggle("psheet-open", open); };
    menuBtn.addEventListener("click", () => setSheet(sheet.hidden));
    sheet.querySelector(".pclose").addEventListener("click", () => setSheet(false));
    sheet.addEventListener("click", (e) => { if (e.target === sheet || e.target.closest("a")) setSheet(false); });
    sheet.querySelector(".pall input").addEventListener("change", (e) => {
      document.documentElement.classList.toggle("pall-text", e.target.checked);
      setTimeout(() => window.G && G.redrawAll && G.redrawAll(), 50);
    });

    // Bottom bar: previous and next part.
    const nav = document.createElement("nav");
    nav.id = "pnav"; nav.setAttribute("aria-label", "Previous and next part");
    nav.innerHTML = '<a class="pprev" href="#">‹ Prev</a><span class="pidx"></span><a class="pnext" href="#">Next ›</a>';
    document.body.append(nav);

    // Follow the part on screen.
    const cur = bar.querySelector(".pcur");
    const show = (i) => {
      const p = parts[i];
      cur.textContent = p ? `${p.num} · ${p.title}` : "GNSS course";
      cur.setAttribute("href", p ? "#" + p.id : "#top");
      nav.querySelector(".pidx").textContent = p ? `${i + 1} of ${parts.length}` : `Intro · ${parts.length} parts`;
      const prev = nav.querySelector(".pprev"), next = nav.querySelector(".pnext");
      prev.classList.toggle("off", i <= 0); prev.setAttribute("href", i > 0 ? "#" + parts[i - 1].id : "#top");
      const ni = Math.min(parts.length - 1, i + 1);
      next.classList.toggle("off", i >= parts.length - 1); next.setAttribute("href", "#" + parts[ni].id);
      sheet.querySelectorAll("a").forEach((a, k) => a.classList.toggle("on", k === i));
    };
    show(-1);
    const io = new IntersectionObserver((ents) => {
      for (const e of ents) if (e.isIntersecting) show(parts.findIndex((p) => p.el === e.target));
    }, { rootMargin: "-45% 0px -50% 0px" });
    parts.forEach((p) => io.observe(p.el));
    if (window.G && G.redrawAll) setTimeout(G.redrawAll, 50);
  }

  const apply = () => {
    document.documentElement.classList.toggle("phone", mq.matches);
    if (mq.matches) build();
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apply); else apply();
  mq.addEventListener && mq.addEventListener("change", apply);
})();
