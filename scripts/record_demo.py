"""Record the HANDOFF §12 demo as a video.

Drives the running app (default http://localhost:8000) in your installed Chrome via Playwright,
with step captions and a visible cursor, and writes media/leak-radar-demo.mp4.

    .venv/bin/pip install playwright imageio-ffmpeg     # once
    make api                                            # in another terminal
    .venv/bin/python scripts/record_demo.py [--url ...] [--dark]
"""
from __future__ import annotations

import argparse
import base64
import time
import shutil
import subprocess
from pathlib import Path

from playwright.sync_api import Locator, Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "media"
W, H = 1440, 900

OVERLAY_JS = """
(() => {
  if (window.__demo) return;
  window.__demo = true;
  const style = document.createElement('style');
  style.textContent = `
    #demo-cursor { position: fixed; z-index: 99999; width: 18px; height: 18px; margin: -9px 0 0 -9px;
      border-radius: 50%; background: rgba(42,120,214,.35); border: 2px solid #2a78d6; pointer-events: none;
      transition: transform .12s ease; left: -50px; top: -50px; }
    #demo-cursor.down { transform: scale(.7); background: rgba(42,120,214,.6); }
    #demo-caption { position: fixed; z-index: 99998; left: 50%; top: 64px; transform: translateX(-50%);
      max-width: 880px; padding: 10px 18px; border-radius: 12px; background: rgba(11,11,11,.86); color: #fff;
      font: 600 17px/1.4 system-ui, -apple-system, sans-serif; box-shadow: 0 8px 30px rgba(0,0,0,.25);
      opacity: 0; transition: opacity .35s ease; pointer-events: none; text-align: center; }
    #demo-caption small { display: block; font-weight: 450; font-size: 14px; opacity: .8; margin-top: 2px; }
    #demo-card { position: fixed; inset: 0; z-index: 100000; display: grid; place-items: center;
      background: #0b0b0b; color: #f0efec; font-family: system-ui, -apple-system, sans-serif;
      transition: opacity .6s ease; text-align: center; }
    #demo-card h1 { font-size: 46px; margin: 0 0 10px; letter-spacing: -.02em; }
    #demo-card p { font-size: 20px; margin: 6px 0; color: #c3c2b7; }
    #demo-card .tag { display: inline-block; margin-top: 18px; padding: 4px 12px; border-radius: 999px;
      background: #fdf1d6; color: #7a5200; font-size: 14px; font-weight: 600; }
    #demo-card .fine { font-size: 13px; color: #898781; margin-top: 26px; }
    .demo-highlight { outline: 3px solid #eb6834 !important; outline-offset: 3px; border-radius: 8px; }
  `;
  document.head.appendChild(style);
  const cur = document.createElement('div'); cur.id = 'demo-cursor'; document.body.appendChild(cur);
  const cap = document.createElement('div'); cap.id = 'demo-caption'; document.body.appendChild(cap);
  addEventListener('mousemove', e => { cur.style.left = e.clientX + 'px'; cur.style.top = e.clientY + 'px'; }, true);
  addEventListener('mousedown', () => cur.classList.add('down'), true);
  addEventListener('mouseup', () => cur.classList.remove('down'), true);
  window.__caption = (html) => { cap.innerHTML = html || ''; cap.style.opacity = html ? 1 : 0; };
  window.__card = (html) => {
    let c = document.getElementById('demo-card');
    if (!html) { if (c) { c.style.opacity = 0; setTimeout(() => c.remove(), 650); } return; }
    if (!c) { c = document.createElement('div'); c.id = 'demo-card'; document.body.appendChild(c); }
    c.innerHTML = html; c.style.opacity = 1;
  };
})();
"""

DISCLAIMER = ("Independent concept project. Not affiliated with or endorsed by Razorpay. "
              "Product names are used only to illustrate fit.")


class Demo:
    def __init__(self, page: Page, speed: float):
        self.page = page
        self.speed = speed

    def pause(self, s: float) -> None:
        self.page.wait_for_timeout(s * self.speed * 1000)

    def caption(self, title: str, sub: str = "") -> None:
        html = title + (f"<small>{sub}</small>" if sub else "")
        self.page.evaluate("h => window.__caption(h)", html)

    def card(self, html: str | None) -> None:
        self.page.evaluate("h => window.__card(h)", html)

    def move_to(self, loc: Locator) -> tuple[float, float]:
        loc.scroll_into_view_if_needed()
        b = loc.bounding_box()
        x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
        self.page.mouse.move(x, y, steps=28)
        return x, y

    def click(self, loc: Locator) -> None:
        self.move_to(loc)
        self.pause(0.35)
        loc.click()

    def ask(self, text: str) -> None:
        box = self.page.get_by_label("Message")
        self.click(box)
        box.press_sequentially(text, delay=38 * self.speed)
        self.pause(0.4)
        box.press("Enter")

    def wait_reply(self) -> None:
        """Wait until the assistant has finished (input re-enabled)."""
        self.page.wait_for_function("() => !document.querySelector('input[aria-label=Message]').disabled",
                                    timeout=60000)

    def show(self, loc: Locator, block: str = "start") -> None:
        loc.evaluate(f"el => el.scrollIntoView({{behavior: 'smooth', block: '{block}'}})")
        self.pause(1.0)

    def highlight(self, loc: Locator, seconds: float) -> None:
        loc.evaluate("el => el.classList.add('demo-highlight')")
        self.pause(seconds)
        loc.evaluate("el => el.classList.remove('demo-highlight')")


def run(page: Page, d: Demo, start_recording) -> None:
    page.evaluate(OVERLAY_JS)

    # Title card
    d.card("<div><h1>Leak Radar</h1><p>An AI cross-sell copilot for payment-gateway account managers</p>"
           "<p>Find the leak → size it in ₹ → show the evidence → recommend the fix → draft outreach</p>"
           "<span class='tag'>Synthetic demo data</span>"
           f"<div class='fine'>{DISCLAIMER}</div></div>")
    d.pause(0.5)
    start_recording()          # start once the title card is painted, so it is the first frame
    d.pause(4.5)
    d.card(None)
    d.pause(1.2)

    # 1. Ranked leaks
    d.caption("1 · Ask where merchants are losing money",
              "The agent ranks detected leaks by recoverable ₹/month")
    d.ask("Which of my merchants are losing the most this month?")
    d.wait_reply()
    table = page.locator("section[aria-label='Merchant leak table']").last
    d.show(table)
    d.caption("Every ₹ figure comes from the detectors, never from the LLM",
              "The UI component receives leak IDs and fetches the numbers itself")
    first_row = table.locator("tr.clickable").first
    d.move_to(first_row)
    d.pause(3.0)
    d.highlight(table.locator(".rec-config").first, 2.2)

    # 2. Drill into the top leak
    d.caption("2 · Click the top merchant", "Leak card, root cause and daily trend")
    d.pause(1.0)
    d.click(first_row)
    d.wait_reply()
    leak = page.locator("section[aria-label^='Leak L']").last
    d.show(leak)
    d.pause(2.5)
    d.caption("“How we calculated this” — every step and assumption is stored with the leak")
    summary = leak.locator("summary")
    d.click(summary)
    d.pause(4.5)
    d.caption("Root cause by failure layer, and the trend against the baseline",
              "Customer-caused failures (insufficient funds) are never counted as a leak")
    root = page.locator("section[aria-label='Root cause']").last
    d.show(root, "center")
    d.pause(1.5)
    trend = page.locator("section[aria-label='Trend'] .recharts-surface").last
    b = trend.bounding_box()
    for frac in (0.25, 0.45, 0.65, 0.8):
        page.mouse.move(b["x"] + b["width"] * frac, b["y"] + b["height"] * 0.5, steps=22)
        d.pause(0.7)
    d.pause(1.5)

    # 3. Draft, edit, approve
    d.caption("3 · Draft outreach in a consultative tone",
              "The LLM writes the wording; figures are filled in by code from the leak record")
    d.ask("Draft a pitch, consultative tone")
    d.wait_reply()
    draft = page.locator("section[aria-label^='Draft P']").last
    d.show(draft)
    d.pause(3.0)
    d.caption("The AM edits the draft and approves it", "Approving records a decision — nothing is ever sent")
    ta = draft.get_by_label("Draft text")
    d.click(ta)
    ta.evaluate("el => { el.focus(); const p = el.value.indexOf('Best regards'); "
                "el.setSelectionRange(p, p); el.scrollTop = el.scrollHeight; }")
    d.pause(0.5)
    page.keyboard.type("P.S. Happy to walk you through the underlying payments first.\n\n", delay=30 * d.speed)
    d.pause(1.0)
    d.click(draft.get_by_role("button", name="Approve"))
    draft.locator(".status-approved").wait_for()
    d.pause(2.5)

    # 4. Evidence
    d.caption("4 · “Show me why” — the raw payments behind the number",
              "Failed rows first, with the filter that reproduces them")
    d.ask("Show me why")
    d.wait_reply()
    ev = page.locator("section[aria-label='Evidence']").last
    d.show(ev)
    d.pause(3.0)
    d.click(ev.get_by_role("button", name="Next"))
    d.pause(2.5)

    # 5. Config review
    d.caption("5 · Merchants who already have the product but still leak",
              "Recommendation switches to a configuration review instead of a cross-sell")
    d.ask("Which merchants already have Optimizer but still leak?")
    d.wait_reply()
    t2 = page.locator("section[aria-label='Merchant leak table']").last
    d.show(t2)
    d.pause(4.0)

    # 6. Footer eval
    d.caption("6 · Detectors are evaluated against planted ground truth",
              "Precision, recall and ₹ error on the demo data and 10 unseen seeds")
    d.highlight(page.locator("footer.footer"), 4.5)
    d.caption("")
    d.pause(0.5)

    d.card("<div><h1>Leak Radar</h1>"
           "<p>Numbers from code · components by ID · number guard on every reply · human approval</p>"
           "<p>30/30 planted leaks found · 0 false positives</p>"
           "<span class='tag'>Synthetic demo data</span>"
           f"<div class='fine'>{DISCLAIMER}</div></div>")
    d.pause(4.0)


class Recorder:
    """Captures Chrome's screencast frames (CDP) and encodes them with the bundled ffmpeg."""

    def __init__(self, page: Page, frames_dir: Path):
        self.dir = frames_dir
        self.frames: list[tuple[float, Path]] = []
        self.cdp = page.context.new_cdp_session(page)
        self.cdp.on("Page.screencastFrame", self._on_frame)

    def _on_frame(self, ev: dict) -> None:
        path = self.dir / f"f{len(self.frames):06d}.jpg"
        path.write_bytes(base64.b64decode(ev["data"]))
        self.frames.append((ev["metadata"]["timestamp"], path))
        self.cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})

    def start(self) -> None:
        self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": W, "maxHeight": H,
                                               "everyNthFrame": 1})

    def stop(self) -> None:
        self.cdp.send("Page.stopScreencast")
        self.end = time.time()

    def encode(self, mp4: Path) -> None:
        import imageio_ffmpeg

        # Chrome only sends a frame when the page changes: hold each frame until the next one.
        lines = []
        for (t, p), (t2, _) in zip(self.frames, self.frames[1:] + [(self.frames[-1][0] + 1.0, None)]):
            lines += [f"file '{p.name}'", f"duration {max(t2 - t, 0.001):.4f}"]
        lines.append(f"file '{self.frames[-1][1].name}'")
        (self.dir / "frames.txt").write_text("\n".join(lines))
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", str(self.dir / "frames.txt"), "-vf", f"fps=30,scale={W}:{H}:flags=lanczos,format=yuv420p",
                        "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-movflags", "+faststart", str(mp4)],
                       check=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--dark", action="store_true")
    ap.add_argument("--speed", type=float, default=1.0, help="pause multiplier (0.5 = faster)")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    frames_dir = OUT / "_frames"
    shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1,
                                  color_scheme="dark" if args.dark else "light")
        page = ctx.new_page()
        rec = Recorder(page, frames_dir)
        page.goto(args.url, wait_until="networkidle")
        try:
            run(page, Demo(page, args.speed), rec.start)
        finally:
            rec.stop()
            ctx.close()
            browser.close()
    mp4 = OUT / "leak-radar-demo.mp4"
    rec.encode(mp4)
    dur = rec.frames[-1][0] - rec.frames[0][0]
    shutil.rmtree(frames_dir, ignore_errors=True)
    print(f"wrote {mp4} ({len(rec.frames)} frames, {dur:.0f}s, {mp4.stat().st_size / 1e6:.1f} MB)")
