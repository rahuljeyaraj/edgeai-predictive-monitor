#!/usr/bin/env python3
"""
Single-page control panel for MULTIPLE satellite_node_sim.py copies at once
-- for demoing N sim nodes together without N browser tabs.

Each satellite_node_sim.py copy already runs its own little REST API
(/state, /online, /config, /rate) on its own --ui-port -- this script does
NOT replace or modify that. It just runs alongside the already-started
copies (see ../start_sim_nodes_panel.sh) and:
  - polls every node's /state itself (server-side, so no browser CORS
    issue talking to N different ports) and serves ONE aggregated page
  - lets you tick which nodes to act on and flip them online/offline, or
    set their capture file, individually or all ticked ones in one go
  - shows each node's status as the same colored dot satellite_node_sim.py's
    own page shows (real LED color/mode as pushed by the base station over
    MQTT, not just a local online/offline flag)

Deliberately NOT shown here (use a node's own /<ui-port>/ page for these,
they're per-demo debug knobs, not part of the "flip N nodes on and watch
them show up" story this panel exists for): spectrum plots, live scalar
readout, accel/mic/scalar channel selection, bin counts, publish rate.
Every node keeps whatever channel/scalar config it already has (each of
satellite_node_sim.py's own defaults, or whatever start_sim_nodes_panel.sh
pre-configured) -- this panel only ever touches "file" when you use its
Capture File controls, never accel/mic/scalars/bin counts.

Usage (normally launched for you by ../start_sim_nodes_panel.sh, not run
directly):
    python3 tools/sim_node_panel.py --node-ports 9101 9102 9103 \\
        --panel-host 0.0.0.0 --panel-port 9100
"""
import argparse
import concurrent.futures
import json
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TIMEOUT_S = 2.0


def _node_get(port: int, path: str) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=TIMEOUT_S) as r:
        return json.loads(r.read())


def _node_post(port: int, path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return json.loads(r.read())


def fetch_state(port: int) -> dict:
    try:
        state = _node_get(port, "/state")
        state["port"] = port
        state["reachable"] = True
        return state
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as e:
        return {"port": port, "reachable": False, "error": str(e), "node_id": None, "online": False,
                "led": {"rgb": "#000000", "mode": "const", "period_ms": 0}, "file": None, "files": []}


def fetch_all(ports):
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(ports))) as pool:
        results = list(pool.map(fetch_state, ports))
    results.sort(key=lambda s: s["port"])
    return results


def set_online(port: int, online: bool) -> dict:
    try:
        _node_post(port, "/online", {"online": online})
        return fetch_state(port)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return {"port": port, "reachable": False, "error": str(e)}


def set_file(port: int, file_name: str) -> dict:
    """Changes only the capture file -- resends the node's OWN current
    accel/accel_fused/mic/scalars/bin counts unchanged, since /config
    overwrites the whole config, not just the file (see set_config() in
    satellite_node_sim.py)."""
    try:
        current = _node_get(port, "/state")
        _node_post(port, "/config", {
            "file": file_name,
            "accel": current["accel"],
            "accel_fused": current["accel_fused"],
            "mic": current["mic"],
            "scalars": current["scalars"],
            "accel_bin_count": current["accel_bin_count"],
            "mic_bin_count": current["mic_bin_count"],
        })
        return fetch_state(port)
    except (urllib.error.URLError, OSError, TimeoutError, KeyError) as e:
        return {"port": port, "reachable": False, "error": str(e)}


PAGE_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>EPM Sim Node Panel</title>
<style>
  body { font-family: sans-serif; background: #111; color: #eee; margin: 0; padding: 1.5rem; }
  h1 { font-size: 1.3rem; color: #9cf; font-weight: normal; }
  .bulk-bar {
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.7rem;
    background: #1b1b1b; border: 1px solid #333; border-radius: 8px;
    padding: 0.8rem 1rem; margin-bottom: 1rem;
  }
  .bulk-bar select { font-size: 0.95rem; padding: 0.3rem; min-width: 14rem; }
  .bulk-bar button { font-size: 0.95rem; padding: 0.45rem 0.9rem; cursor: pointer; }
  .bulk-bar label { display: flex; align-items: center; gap: 0.4rem; font-size: 0.95rem; }
  table { border-collapse: collapse; width: 100%; }
  th, td { padding: 0.6rem 0.7rem; text-align: left; border-bottom: 1px solid #2a2a2a; }
  th { font-size: 0.8rem; color: #9ab; font-weight: normal; text-transform: uppercase; letter-spacing: 0.03em; }
  td { font-size: 0.95rem; }
  tr.unreachable { opacity: 0.45; }
  .dot {
    width: 22px; height: 22px; border-radius: 50%; display: inline-block;
    box-shadow: 0 0 10px currentColor; vertical-align: middle;
  }
  .dot.mode-const { animation: none; opacity: 1; }
  .dot.mode-breathe { animation: breathe linear infinite; }
  .dot.mode-strobe { animation: strobe steps(1) infinite; }
  @keyframes breathe { 0%, 100% { opacity: 0.25; } 50% { opacity: 1; } }
  @keyframes strobe { 0%, 49% { opacity: 1; } 50%, 100% { opacity: 0.15; } }
  .status-text { font-size: 0.85rem; margin-left: 0.6rem; }
  .status-text.online { color: #6f6; }
  .status-text.offline { color: #888; }
  .node-id { font-family: monospace; font-size: 1rem; }
  .port { font-family: monospace; font-size: 0.75rem; color: #778; }
  select.file-select { font-size: 0.9rem; padding: 0.25rem; max-width: 22rem; }
  button.online-toggle { font-size: 0.85rem; padding: 0.35rem 0.7rem; cursor: pointer; }
  .error-text { font-size: 0.75rem; color: #f87171; }
</style>
</head>
<body>
  <h1>Satellite Sim Nodes</h1>

  <div class="bulk-bar">
    <label><input type="checkbox" id="select-all" onchange="toggleSelectAll()"> Select all</label>
    <select id="bulk-file"></select>
    <button onclick="applyBulkFile()">Apply file to selected</button>
    <button onclick="applyBulkOnline(true)">Go Online (selected)</button>
    <button onclick="applyBulkOnline(false)">Go Offline (selected)</button>
  </div>

  <table>
    <thead>
      <tr>
        <th></th>
        <th>Status</th>
        <th>Node</th>
        <th>Capture File</th>
        <th></th>
      </tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>

<script>
let nodes = [];
const checked = new Set();

// While ANY <select> on the page is focused (dropdown open, or about to be
// clicked), skip render() entirely -- not just that one select's options.
// Per-select "don't touch it while it's focused" guards weren't enough on
// their own: patching sibling cells in the SAME row (status text, dot
// color, checkbox) still reflows that row, and a reflow near an open
// native dropdown can shift/close its popup even though the select
// element itself was never touched. Freezing the whole table for the
// half-second a dropdown is open is imperceptible; render() catches up
// immediately on focusout.
let selectFocused = false;
document.addEventListener("focusin", (e) => {
  if (e.target.tagName === "SELECT") selectFocused = true;
});
document.addEventListener("focusout", (e) => {
  if (e.target.tagName === "SELECT") {
    selectFocused = false;
    render();
  }
});

function fmtPort(p) { return "port " + p; }

// Row DOM is built ONCE per port and patched in place on every poll --
// never innerHTML-replaced -- so an open <select> dropdown doesn't get
// yanked out from under the mouse before you can click an option (that's
// what a full rebuild does: the browser closes any native dropdown whose
// element just got destroyed and recreated, even with identical content).
const rowEls = new Map();  // port -> {tr, checkbox, dot, statusText, nodeId, portLabel, errorDiv, select, onlineBtn}

function buildRow(port) {
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td><input type="checkbox"></td>
    <td>
      <span class="dot mode-const"></span>
      <span class="status-text"></span>
    </td>
    <td>
      <div class="node-id"></div>
      <div class="port"></div>
      <div class="error-text"></div>
    </td>
    <td><select class="file-select"></select></td>
    <td><button class="online-toggle"></button></td>`;
  const refs = {
    tr,
    checkbox: tr.querySelector('input[type="checkbox"]'),
    dot: tr.querySelector(".dot"),
    statusText: tr.querySelector(".status-text"),
    nodeId: tr.querySelector(".node-id"),
    portLabel: tr.querySelector(".port"),
    errorDiv: tr.querySelector(".error-text"),
    select: tr.querySelector(".file-select"),
    onlineBtn: tr.querySelector(".online-toggle"),
  };
  refs.checkbox.addEventListener("change", () => toggleOne(port, refs.checkbox.checked));
  refs.select.addEventListener("change", () => setFile(port, refs.select.value));
  refs.onlineBtn.addEventListener("click", () => setOnline(port, refs.onlineBtn.dataset.nextOnline === "1"));
  document.getElementById("rows").appendChild(tr);
  rowEls.set(port, refs);
  return refs;
}

function updateRow(refs, n) {
  refs.tr.className = n.reachable === false ? "unreachable" : "";

  refs.checkbox.checked = checked.has(n.port);

  const mode = (n.led && n.led.mode) || "const";
  const rgb = (n.led && n.led.rgb) || "#000000";
  const periodMs = (n.led && n.led.period_ms) || 1000;
  refs.dot.className = "dot mode-" + mode;
  refs.dot.style.background = rgb;
  refs.dot.style.color = rgb;
  refs.dot.style.animationDuration = periodMs + "ms";

  const statusLabel = n.reachable === false ? "unreachable" : (n.online ? "online" : "offline");
  refs.statusText.textContent = statusLabel;
  refs.statusText.className = "status-text " + (n.online ? "online" : "offline");

  refs.nodeId.textContent = n.node_id || "(unknown)";
  refs.portLabel.textContent = fmtPort(n.port);
  refs.errorDiv.textContent = n.error || "";

  // Never touch the <select>'s options/value while the user has it
  // focused (open dropdown, or about to click an option) -- just skip
  // this node's file-select update until the next poll after they're done.
  if (document.activeElement !== refs.select) {
    const files = n.files || [];
    const optionsKey = files.join(",");
    if (refs.select.dataset.optionsKey !== optionsKey) {
      refs.select.innerHTML = files.map(f => `<option value="${f}">${f}</option>`).join("");
      refs.select.dataset.optionsKey = optionsKey;
    }
    if (refs.select.value !== (n.file || "")) {
      refs.select.value = n.file || "";
    }
  }

  refs.onlineBtn.textContent = n.online ? "Go Offline" : "Go Online";
  refs.onlineBtn.dataset.nextOnline = n.online ? "0" : "1";
}

function render() {
  const allFiles = new Set();
  for (const n of nodes) for (const f of (n.files || [])) allFiles.add(f);
  const bulkSelect = document.getElementById("bulk-file");
  const sortedFiles = Array.from(allFiles).sort();
  if (document.activeElement !== bulkSelect && bulkSelect.dataset.files !== sortedFiles.join(",")) {
    bulkSelect.innerHTML = sortedFiles.map(f => `<option value="${f}">${f}</option>`).join("");
    bulkSelect.dataset.files = sortedFiles.join(",");
  }

  for (const n of nodes) {
    const refs = rowEls.get(n.port) || buildRow(n.port);
    updateRow(refs, n);
  }

  document.getElementById("select-all").checked =
    nodes.length > 0 && nodes.every(n => checked.has(n.port));
}

async function poll() {
  try {
    const r = await fetch("/api/nodes");
    nodes = await r.json();
    if (!selectFocused) render();
  } catch (e) {
    // transient -- next poll retries
  }
}

function toggleOne(port, isChecked) {
  if (isChecked) checked.add(port); else checked.delete(port);
}

function toggleSelectAll() {
  const all = document.getElementById("select-all").checked;
  checked.clear();
  if (all) for (const n of nodes) checked.add(n.port);
  render();
}

async function setOnline(port, online) {
  await fetch("/api/online", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ports: [port], online}),
  });
  poll();
}

async function setFile(port, file) {
  await fetch("/api/config", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ports: [port], file}),
  });
  poll();
}

async function applyBulkOnline(online) {
  if (!checked.size) return;
  await fetch("/api/online", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ports: Array.from(checked), online}),
  });
  poll();
}

async function applyBulkFile() {
  if (!checked.size) return;
  const file = document.getElementById("bulk-file").value;
  if (!file) return;
  await fetch("/api/config", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ports: Array.from(checked), file}),
  });
  poll();
}

setInterval(poll, 800);
poll();
</script>
</body>
</html>
"""


def build_httpd(panel_host: str, panel_port: int, node_ports):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # quiet by default, matches satellite_node_sim.py

        def _send_json(self, obj, status=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                body = PAGE_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/nodes":
                self._send_json(fetch_all(node_ports))
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                self._send_json({"error": "bad json"}, status=400)
                return

            ports = [p for p in (body.get("ports") or []) if p in node_ports]
            if not ports:
                self._send_json({"error": "no valid ports"}, status=400)
                return

            if self.path == "/api/online":
                online = bool(body.get("online"))
                with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(ports))) as pool:
                    list(pool.map(lambda p: set_online(p, online), ports))
            elif self.path == "/api/config":
                file_name = body.get("file")
                if not file_name:
                    self._send_json({"error": "missing file"}, status=400)
                    return
                with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(ports))) as pool:
                    list(pool.map(lambda p: set_file(p, file_name), ports))
            else:
                self.send_response(404)
                self.end_headers()
                return
            self._send_json(fetch_all(node_ports))

    return ThreadingHTTPServer((panel_host, panel_port), Handler)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--node-ports", type=int, nargs="+", required=True,
                         help="UI ports of already-running satellite_node_sim.py copies")
    parser.add_argument("--panel-host", default="0.0.0.0")
    parser.add_argument("--panel-port", type=int, default=9100)
    args = parser.parse_args()

    httpd = build_httpd(args.panel_host, args.panel_port, args.node_ports)
    print(f"Sim node panel: http://localhost:{args.panel_port}/ "
          f"(controlling {len(args.node_ports)} node(s) on ports {args.node_ports})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
