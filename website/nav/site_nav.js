/* site_nav.js - A small site menu for pages that keep their own layout.
 *
 * The GNSS course (/course/) and the mission planner (/sim/) are full
 * applications with their own top bars, so they cannot take the site's
 * normal top bar. build_site.py adds this script to both. It draws one
 * round menu button (☰). A click opens a panel with the site's pages.
 *
 * The build sets window.HERON_NAV before this script loads:
 *   { root: "../", here: "course", pos: "top-right" | "bottom-left",
 *     logo: "img/heron-logo-white-sky.png", links: [{ key, label, path }, ...] }
 * The links come from site.json ("nav"), so all pages list the same set.
 */
(function () {
  "use strict";
  const cfg = window.HERON_NAV;
  if (!cfg) return;

  const css = `
  #heron-nav{position:fixed;z-index:7000;font:15px/1.3 system-ui,"Segoe UI",sans-serif}
  #heron-nav.top-right{top:12px;right:12px}
  #heron-nav.bottom-left{bottom:12px;left:12px}
  #heron-nav .hn-btn{width:44px;height:44px;border-radius:50%;border:1px solid rgba(255,255,255,.25);
    background:rgba(13,34,48,.92);color:#fff;cursor:pointer;display:grid;place-items:center;
    box-shadow:0 6px 20px rgba(0,0,0,.35);padding:0}
  #heron-nav .hn-btn:hover{background:rgba(20,52,72,.96)}
  #heron-nav .hn-btn:focus-visible{outline:2px solid #8fd0ea;outline-offset:2px}
  #heron-nav .hn-btn svg{width:22px;height:22px}
  #heron-nav .hn-panel{position:absolute;min-width:230px;background:#0f2230;color:#e6eef3;border-radius:14px;
    border:1px solid rgba(255,255,255,.12);box-shadow:0 14px 40px rgba(0,0,0,.45);padding:10px;display:none}
  #heron-nav.open .hn-panel{display:block}
  #heron-nav.top-right .hn-panel{top:52px;right:0}
  #heron-nav.bottom-left .hn-panel{bottom:52px;left:0}
  #heron-nav .hn-head{display:flex;align-items:center;gap:10px;padding:4px 6px 10px;border-bottom:1px solid rgba(255,255,255,.1);margin-bottom:6px}
  #heron-nav .hn-head img{width:34px;height:auto}
  #heron-nav .hn-head b{letter-spacing:.14em;font-size:14px}
  #heron-nav a{display:block;padding:9px 10px;border-radius:8px;color:#e6eef3;text-decoration:none;font-weight:600}
  #heron-nav a:hover{background:rgba(143,208,234,.14)}
  #heron-nav a[aria-current]{color:#8fd0ea}
  #heron-nav a[aria-current]::before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;background:#8fd0ea;margin-right:8px;vertical-align:middle}
  @media print{#heron-nav{display:none}}`;

  function build() {
    const style = document.createElement("style");
    style.textContent = css;
    document.head.append(style);
    const nav = document.createElement("nav");
    nav.id = "heron-nav";
    nav.className = cfg.pos || "top-right";
    nav.setAttribute("aria-label", "HERON site");
    const links = cfg.links.map((l) =>
      `<a href="${cfg.root}${l.path}"${l.key === cfg.here ? ' aria-current="page"' : ""}>${l.label}</a>`).join("");
    nav.innerHTML =
      `<button type="button" class="hn-btn" aria-expanded="false" aria-label="Site menu" title="HERON site menu">` +
      `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"/></svg></button>` +
      `<div class="hn-panel"><div class="hn-head"><img src="${cfg.root}${cfg.logo}" alt=""><b>HERON</b></div>${links}</div>`;
    document.body.append(nav);
    const btn = nav.querySelector(".hn-btn");
    const set = (open) => { nav.classList.toggle("open", open); btn.setAttribute("aria-expanded", String(open)); };
    btn.addEventListener("click", (e) => { e.stopPropagation(); set(!nav.classList.contains("open")); });
    // Close on a click outside the menu, and on Escape. Escape is not passed
    // on to the page, so the course's own Esc (leave present mode) still works
    // when the menu is closed.
    document.addEventListener("click", (e) => { if (!nav.contains(e.target)) set(false); });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && nav.classList.contains("open")) { set(false); btn.focus(); e.stopPropagation(); }
    }, true);
  }
  if (document.body) build(); else document.addEventListener("DOMContentLoaded", build);
})();
