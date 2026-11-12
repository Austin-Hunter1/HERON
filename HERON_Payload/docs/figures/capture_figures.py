# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright>=1.40"]
# ///
"""Captures example figures of the HERON payload changes from the local demo pages (Edge, headless).

Usage: uv run capture_figures.py <output folder>
Needs the demo payload page on http://127.0.0.1:8091 and the download page on http://localhost:8771.
"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
NUC = "http://127.0.0.1:8091/"
LOCAL = "http://localhost:8771/"


def shot(page, selector, name, pad=12):
    """Screenshot one element (with a little margin) at 2x pixel density."""
    # Page coordinates (not window coordinates), so an element below the window edge is captured whole.
    box = page.locator(selector).first.evaluate(
        "e => { const r = e.getBoundingClientRect(); return {x: r.x + scrollX, y: r.y + scrollY, width: r.width, height: r.height}; }")
    page.screenshot(path=str(OUT / name), full_page=True,
                    clip=dict(x=max(0, box["x"] - pad), y=max(0, box["y"] - pad),
                              width=box["width"] + 2 * pad, height=box["height"] + 2 * pad))
    print("saved", name)


with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page(viewport=dict(width=1500, height=1000), device_scale_factor=2)

    # ---- Payload Setup: load the default setup (simulated radios)
    page.goto(NUC)
    page.wait_for_selector("#applySetup")
    if page.is_enabled("#applySetup"):
        page.click("#applySetup")
    page.wait_for_function("window.HERON && window.HERON.state && window.HERON.state.status.state === 'READY'")
    page.add_style_tag(content=".side, .side * { position: static !important; } .sweep-scroll, .drops-scroll "
                               "{ max-height: none !important; } .toast, #toast { display: none !important; }")
    time.sleep(3)                                         # let the "Loaded" notice fade

    # Figure 1: the Recording panel (write method, buffers, 10 MHz / PPS) and a radio's Profile / Bandwidth.
    page.select_option('[data-k="clock_source"]', "external")
    page.select_option('[data-k="time_source"]', "external")
    shot(page, 'section[aria-labelledby="recHeading"]', "fig1_recording_settings.png")
    page.select_option("select.profile", "0")         # the collect wizard's "L5 Band" profile
    shot(page, "article.radio, .radio", "fig2_radio_profile_bandwidth.png")
    page.select_option('[data-k="clock_source"]', "internal")
    page.select_option('[data-k="time_source"]', "internal")

    # Figure 3: gain sweep (short trials so the demo is quick).
    page.fill("#gSeconds", "1")
    page.click("#runGain")
    page.wait_for_function("window.HERON.state.sweep && window.HERON.state.sweep.running", timeout=10000)
    page.wait_for_function("window.HERON.state.sweep && !window.HERON.state.sweep.running", timeout=180000)
    time.sleep(1.5)
    page.evaluate("document.querySelector('.sweep-form').open = false")
    # The results file path is the demo's temp folder (on the NUC it is /var/lib/heron/sweeps); leave it out.
    page.evaluate("[...document.querySelectorAll('#sweepResult p')].filter(p => p.textContent.startsWith('Saved on')).forEach(p => p.remove())")
    shot(page, "section.sweeps", "fig3_gain_sweep_results.png")

    # Figure 4: buffer sweep (2 x 2 x 2 trials).
    page.evaluate("document.querySelectorAll('.sweep-form').forEach((d, i) => d.open = i === 1)")
    page.fill("#bSpb", "2000, 10000")
    page.fill("#bArgs", "-\nnum_recv_frames=256")
    page.fill("#bFile", "0")
    page.fill("#bSeconds", "1")
    page.click("#runBuffer")
    page.wait_for_function("window.HERON.state.sweep && window.HERON.state.sweep.running", timeout=10000)
    page.wait_for_function("window.HERON.state.sweep && !window.HERON.state.sweep.running", timeout=180000)
    time.sleep(1.5)
    page.evaluate("[...document.querySelectorAll('#sweepResult p')].filter(p => p.textContent.startsWith('Saved on')).forEach(p => p.remove())")
    shot(page, "#sweepResult", "fig4_buffer_sweep_results.png")

    # Figure 5: drop finder table, then the spectrogram around the second drop with its red marker.
    page.click('#recList [data-s="HERON_20261102_120000"]')
    page.click('[data-drops="b210"]')
    page.wait_for_selector(".drops-table")
    shot(page, ".rec-radio", "fig5_drop_list.png")
    page.locator("[data-show]").nth(1).click()
    page.wait_for_selector(".viewer-plots canvas", timeout=20000)
    time.sleep(1.5)
    shot(page, ".viewer", "fig6_spectrogram_drop_marker.png")

    # Figure 7: the HERON Download page on the laptop, after one download.
    local = browser.new_page(viewport=dict(width=1300, height=900), device_scale_factor=2)
    local.goto(LOCAL)
    local.check("#sim")
    local.wait_for_selector("[data-get]")
    local.fill("[data-exp]", "20261102_Bench_Test")
    local.click("[data-get]")
    local.wait_for_function("document.querySelector('#jobs') && document.querySelector('#jobs').textContent.includes('saved')",
                            timeout=60000)
    time.sleep(1.5)
    shot(local, "body > header, header", "fig7_header.png")
    box = local.locator("main").bounding_box()
    local.screenshot(path=str(OUT / "fig7_heron_download_page.png"),
                     clip=dict(x=0, y=0, width=1300, height=box["y"] + box["height"] + 8))
    print("saved fig7_heron_download_page.png")
    browser.close()
