#!/usr/bin/env python3
"""dashboard.html against the real ControlServer, in a real browser.

The interesting behaviour of the control page is not the serial link (a human
picks the port; Web Serial doesn't exist headless anyway) -- it's what the page
does with what the rig host tells it: how many motors are installed, which of
them the base station has claimed, and which have been tripped. All of that is
plain HTTP, so it can be driven end to end here.

Two real servers are started, not mocks: motor_driver.ControlServer itself, and
a stand-in for the base station's `GET /trip_outputs` (the one cross-origin
fetch the page makes). Only pyserial is stubbed.

Needs playwright. It lives in the base station's venv on this machine:

    ../base-station/python/.venv/bin/python motor-driver/control_page_test.py
"""
import json
import os
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Stub pyserial and the 2 s boot settle, exactly as rig_trip_test.py does.
_fake = types.ModuleType("serial")
_fake.Serial = lambda *a, **k: types.SimpleNamespace(
    write=lambda d: len(d), flush=lambda: None, readline=lambda: b"",
    close=lambda: None, in_waiting=0)
sys.modules["serial"] = _fake

import time as _time  # noqa: E402
_real_sleep = _time.sleep
_time.sleep = lambda s: None
import motor_driver  # noqa: E402
_time.sleep = _real_sleep

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("playwright not installed. Try:\n"
              "  ../base-station/python/.venv/bin/python motor-driver/control_page_test.py")

# How long to wait for a UI change driven by the page's 1 s / 3 s polls.
POLL_TIMEOUT_MS = 8000

FAILURES = []


def check(label, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + label + (f"   <- {extra}" if not cond else ""))
    if not cond:
        FAILURES.append(label)


class FakeBaseStation:
    """Just enough of the base station to answer the one endpoint the control
    page asks it about, with the same permissive CORS header the real API
    sends (api/app.py's CORSMiddleware, allow_origins=["*"])."""

    def __init__(self):
        self.claims = {}          # motor idx -> asset node_id
        self.outputs = []         # what the rig announced, as the API reports it
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/trip_outputs":
                    self.send_response(404)
                    self.end_headers()
                    return
                body = json.dumps({"outputs": [
                    {"idx": idx, "name": f"Motor {idx}",
                      "claimed_by": outer.claims.get(idx)}
                    for idx in outer.outputs
                ]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        self._httpd.shutdown()
        self._httpd.server_close()


def main():
    base = FakeBaseStation()
    rig = motor_driver.Rig("/dev/fake")
    outputs = motor_driver.OutputSet([1])
    announced = []
    outputs.subscribe(announced.append)
    # The fake base station learns what the rig offers the same way the real
    # one does: from the announce.
    outputs.subscribe(lambda o: base.outputs.__setitem__(slice(None), list(o)))
    base.outputs = list(outputs.get())

    control = motor_driver.ControlServer(rig, outputs, 0, directory=HERE,
                                          base_station_url=base.url())
    control.start()
    page_url = f"http://127.0.0.1:{control.port}/"

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(page_url)

        cards = page.locator(".card[id^=card]")
        slots = page.locator(".slot")

        # -- 1. one motor at start ---------------------------------------
        page.wait_for_selector("#card1", timeout=POLL_TIMEOUT_MS)
        check("starts with exactly one motor card", cards.count() == 1, cards.count())
        check("the other two motors are empty slots", slots.count() == 2, slots.count())
        check("fleet counter reads one motor",
               "1 motor" in page.locator("#fleet").inner_text(),
               page.locator("#fleet").inner_text())
        check("sync is hidden with nothing to sync",
               not page.locator("#syncCard").is_visible())
        check("no rig-host warning", not page.locator("#noServer").is_visible())

        # -- 2. adding a motor is a UI act that reaches the broker --------
        page.locator("#slot2").click()
        page.wait_for_selector("#card2", timeout=POLL_TIMEOUT_MS)
        check("adding a motor draws its card", cards.count() == 2, cards.count())
        check("...and re-announces the trip outputs", announced[-1:] == [(1, 2)], announced)
        check("...and updates the fleet counter",
               "2 motors" in page.locator("#fleet").inner_text(),
               page.locator("#fleet").inner_text())
        page.wait_for_function("() => document.getElementById('syncCard').style.display === ''",
                                timeout=POLL_TIMEOUT_MS)
        check("sync appears once there are two motors, naming them",
               page.locator("#syncTxt").inner_text().strip() == "Motor 2 follows Motor 1",
               page.locator("#syncTxt").inner_text())

        # -- 3. the base station claiming a motor shows on the card ------
        base.claims[1] = "base_station"
        page.wait_for_selector("#badges1 .badge.protected", timeout=POLL_TIMEOUT_MS)
        check("a claimed motor shows a PROTECTED badge naming the asset",
               "base_station" in page.locator("#badges1").inner_text(),
               page.locator("#badges1").inner_text())
        check("an unclaimed motor shows no badge",
               page.locator("#badges2").inner_text().strip() == "",
               page.locator("#badges2").inner_text())
        check("fleet counter counts the protected motor",
               "1 protected" in page.locator("#fleet").inner_text(),
               page.locator("#fleet").inner_text())

        # -- 4. a real trip lands on the card ----------------------------
        rig.stop_motor(1)
        page.wait_for_selector("#card1.tripped", timeout=POLL_TIMEOUT_MS)
        check("a tripped motor's card goes into the tripped state", True)
        check("...its status pill says TRIPPED",
               page.locator("#pill1").inner_text().strip() == "TRIPPED",
               page.locator("#pill1").inner_text())
        check("...its run switch is locked out", page.locator("#run1").is_disabled())
        check("...its remove button is locked out", page.locator("#rm1").is_disabled())
        check("...the reason names the asset that faulted",
               "base_station" in page.locator("#tripwhy1").inner_text(),
               page.locator("#tripwhy1").inner_text())
        check("...and the neighbouring motor is untouched",
               not page.locator("#card2").evaluate("el => el.classList.contains('tripped')"))

        # -- 5. reset re-arms it, on the rig, not just on screen ---------
        page.locator("#reset1").click()
        page.wait_for_selector("#card1:not(.tripped)", timeout=POLL_TIMEOUT_MS)
        check("Reset clears the trip on the page", True)
        check("...and on the rig itself", rig.tripped() == [], rig.tripped())
        check("...leaving the motor commandable", page.locator("#run1").is_enabled())

        # -- 6. removing a motor puts the slot back ----------------------
        page.locator("#rm2").click()
        page.wait_for_selector("#slot2", timeout=POLL_TIMEOUT_MS)
        check("removing a motor returns it to an empty slot", cards.count() == 1, cards.count())
        check("...and withdraws it from the announce",
               announced[-1:] == [(1,)], announced)
        check("...and hides sync again", not page.locator("#syncCard").is_visible())

        # -- 7. the page still works with no rig host behind it ----------
        control.stop()
        page.wait_for_selector("#noServer:visible", timeout=POLL_TIMEOUT_MS)
        check("losing the rig host is called out, not silent", True)
        check("...and it falls back to driving every wired motor",
               cards.count() == 3, cards.count())
        check("...with no add/remove slots it couldn't act on",
               slots.count() == 0, slots.count())

        # -- 8. the default mode: the rig host holds no serial port ------
        # The real WSL-host / Windows-browser split. Nothing has physically
        # stopped when a trip arrives; the page has to apply it, and must say
        # so honestly when it can't (headless Chromium has no Web Serial, so
        # "can't" is exactly the state under test).
        portless_rig = motor_driver.Rig()
        portless = motor_driver.ControlServer(
            portless_rig, motor_driver.OutputSet([1]), 0, directory=HERE,
            base_station_url=base.url())
        portless.start()
        page.goto(f"http://127.0.0.1:{portless.port}/")
        page.wait_for_selector("#card1", timeout=POLL_TIMEOUT_MS)
        page.wait_for_selector(".fleet .viapage", timeout=POLL_TIMEOUT_MS)
        check("a portless rig host is flagged in the header",
               "trip via this page" in page.locator("#fleet").inner_text(),
               page.locator("#fleet").inner_text())
        check("...and says protection is off while disconnected",
               "not connected" in page.locator("#fleet").inner_text(),
               page.locator("#fleet").inner_text())

        portless_rig.stop_motor(1)
        page.wait_for_selector("#card1.tripped", timeout=POLL_TIMEOUT_MS)
        check("a trip still reaches the card with no serial on the host", True)
        check("...and does NOT claim the motor stopped when it didn't",
               "NOT APPLIED" in page.locator("#tripwhy1").inner_text(),
               page.locator("#tripwhy1").inner_text())
        check("...while the rig host still records it as tripped",
               portless_rig.tripped() == [1], portless_rig.tripped())

        page.locator("#reset1").click()
        page.wait_for_selector("#card1:not(.tripped)", timeout=POLL_TIMEOUT_MS)
        check("Reset works in this mode too", portless_rig.tripped() == [],
               portless_rig.tripped())
        portless.stop()

        browser.close()

    base.stop()
    print("RESULT: " + ("PASS" if not FAILURES else f"FAIL - {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
