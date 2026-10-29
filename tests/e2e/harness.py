"""Drive the real Streamlit app in headless Chromium with a fake microphone.

No live API call is ever made: ``e2e_app.py`` replaces the Gnani request with a deterministic fake.

CLI:  python harness.py <project_root> [basic|full]
Pytest: BOLKHATA_E2E=1 python -m pytest tests/e2e -q      (needs `pip install playwright` + chromium)
"""
from __future__ import annotations

import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
TRACK_SPY = """
(() => {
  const original = navigator.mediaDevices && navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  if (!original) return;
  window.__bkTracks = [];
  navigator.mediaDevices.getUserMedia = async (c) => {
    const s = await original(c); s.getTracks().forEach((t) => window.__bkTracks.push(t)); return s;
  };
})();
"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _rows(db: Path):
    if not db.exists():
        return []
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT c.name, t.amount_paise, t.transaction_type, t.idempotency_key FROM transactions t "
            "JOIN customers c ON c.id=t.customer_id ORDER BY t.id").fetchall()
    finally:
        con.close()


def run_scenario(root: Path, scenario: str = "basic", log=print) -> dict:
    from playwright.sync_api import sync_playwright

    work = Path(tempfile.mkdtemp(prefix="bk_e2e_"))
    wav, db, stt_log = work / "mic.wav", work / "ledger.sqlite3", work / "stt.jsonl"
    subprocess.check_call([sys.executable, str(HERE / "make_fake_mic.py"), str(wav)])
    port = _free_port()
    env = dict(os.environ, BOLKHATA_PROJECT=str(root), BOLKHATA_DB=str(db), BOLKHATA_E2E_LOG=str(stt_log), GNANI_API_KEY="")
    server = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(HERE / "e2e_app.py"), "--server.port", str(port),
         "--server.headless", "true", "--browser.gatherUsageStats", "false"],
        cwd=root, env=env, stdout=open(work / "streamlit.log", "w"), stderr=subprocess.STDOUT)
    console: list[str] = []
    result: dict = {"workdir": str(work), "console": console}
    t0 = time.time()

    def wait_form(page, seconds=45):
        end = time.time() + seconds
        while time.time() < end:
            if page.get_by_role("button", name="Confirm and save").count():
                return True
            page.wait_for_timeout(300)
        return False

    def click_and_wait_db(page, name, expected_rows):
        page.get_by_role("button", name=name).first.click()
        end = time.time() + 6
        while time.time() < end:
            if len(_rows(db)) >= expected_rows:
                return True
            page.wait_for_timeout(200)
        return False

    try:
        for _ in range(80):
            try:
                if urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=1).status == 200:
                    break
            except OSError:
                time.sleep(0.5)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=[
                "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-audio-capture={wav}", "--autoplay-policy=no-user-gesture-required"])
            context = browser.new_context(permissions=["microphone"])
            context.add_init_script(TRACK_SPY)
            page = context.new_page()
            page.on("console", lambda m: console.append(f"[+{time.time() - t0:5.1f}s] {m.text}"))
            page.on("pageerror", lambda e: console.append(f"PAGEERROR {e}"))
            page.goto(f"http://127.0.0.1:{port}")
            page.wait_for_selector("[data-testid=stSidebar]", timeout=20000)
            for _ in range(10):
                page.get_by_text("Voice Entry", exact=True).first.click()
                if page.get_by_role("heading", name="Voice entry").count():
                    break
                page.wait_for_timeout(500)
            page.wait_for_selector("iframe", timeout=20000)
            frame = next(f for f in page.frames if "component" in f.url)
            frame.click("#start")
            steps: list[str] = []
            if scenario == "basic":
                for n in (1, 2, 3):
                    ok = wait_form(page) and click_and_wait_db(page, "Confirm and save", n)
                    steps.append(f"confirm#{n}:{ok}")
            else:  # full: slow review (utterances queue), reject, stop, restart
                steps.append(f"form1:{wait_form(page)}")
                page.wait_for_timeout(12000)  # next utterance is spoken and queued while we 'review'
                steps.append(f"confirm1:{click_and_wait_db(page, 'Confirm and save', 1)}")
                steps.append(f"form2_released_from_queue:{wait_form(page)}")
                page.get_by_role("button", name="Reject and continue").first.click()
                page.wait_for_timeout(1500)
                steps.append(f"rejected_saved_nothing:{len(_rows(db)) == 1}")
                steps.append(f"form3:{wait_form(page)}")
                steps.append(f"confirm2:{click_and_wait_db(page, 'Confirm and save', 2)}")
                frame.click("#stop")
                page.wait_for_timeout(800)
                result["tracks_after_stop"] = frame.evaluate("window.__bkTracks.map(t => t.readyState)")
                result["status_after_stop"] = frame.inner_text("#status")
                frame.click("#start")  # restart without reloading the app
                steps.append(f"form_after_restart:{wait_form(page)}")
                steps.append(f"confirm3:{click_and_wait_db(page, 'Confirm and save', 3)}")
            result["final_status"] = frame.inner_text("#status")
            frame.click("#stop")
            page.wait_for_timeout(800)
            result["tracks_end"] = frame.evaluate("window.__bkTracks.map(t => t.readyState)")
            result["steps"] = steps
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(10)
        except subprocess.TimeoutExpired:
            server.kill()
    result["rows"] = _rows(db)
    result["stt_log"] = stt_log.read_text() if stt_log.exists() else ""
    result["server_log"] = (work / "streamlit.log").read_text()
    return result


if __name__ == "__main__":
    res = run_scenario(Path(sys.argv[1]).resolve(), sys.argv[2] if len(sys.argv) > 2 else "basic")
    for key in ("steps", "final_status", "status_after_stop", "tracks_after_stop", "tracks_end", "rows"):
        print(key, "=", res.get(key))
    print("STT calls:\n" + res["stt_log"])
    print("\n".join(l for l in res["console"] if "BolKhata" in l and "noise discarded" not in l))
    print("workdir:", res["workdir"])
