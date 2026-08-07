#!/usr/bin/env python3
"""Export interactive i2-style HTML association chart (drag / pan / zoom).

Offline-safe: embeds graph data and uses local vis-network bundle.
"""

from __future__ import annotations

from collections import Counter
import json
import math
import urllib.request
from datetime import datetime
from pathlib import Path

from cdr_crossmatch_i2 import (
    BASE,
    OUT_DIR,
    analyze,
    edge_label,
    fmt_duration,
    fmt_msisdn,
    is_subscriber,
    load_all,
)

OUT_DIR.mkdir(exist_ok=True)
VENDOR = OUT_DIR / "vendor"
VENDOR.mkdir(exist_ok=True)
VIS_JS = VENDOR / "vis-network.min.js"
VIS_URL = "https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"


def ensure_vis_bundle() -> Path:
    if VIS_JS.exists() and VIS_JS.stat().st_size > 100_000:
        return VIS_JS
    print(f"Downloading vis-network -> {VIS_JS}")
    urllib.request.urlretrieve(VIS_URL, VIS_JS)
    return VIS_JS


def build_payload(inv, analysis: dict) -> dict:
    targets = set(str(t) for t in analysis["targets"])

    # Compact per-link timelines for date/time filtering + edge log modal
    events_by_pair: dict[tuple[str, str], list[dict]] = {}
    all_epochs: list[int] = []
    for rec in inv.records:
        if not rec.start or not is_subscriber(rec.bparty):
            continue
        a, b = sorted([str(rec.target), str(rec.bparty)])
        epoch = int(rec.start.timestamp())
        dur = max(0, int(rec.duration or 0))
        events_by_pair.setdefault((a, b), []).append(
            {
                "t": epoch,
                "d": dur,
                "dt": rec.start.strftime("%Y-%m-%d %H:%M:%S"),
                "usage": rec.usage or "",
                "usage_class": rec.usage_class or "",
                "network": rec.network or "",
                "provider": rec.provider or "",
                "imei": rec.imei or "",
                "imsi": rec.imsi or "",
                "lac": rec.lac or "",
                "ci": rec.ci or "",
                "address": rec.address or "",
                "aparty": rec.aparty or "",
                "bparty": rec.bparty or "",
                "target": rec.target or "",
                "source": rec.source_file or "",
            }
        )
        all_epochs.append(epoch)

    # Newest first inside each link
    for key in events_by_pair:
        events_by_pair[key].sort(key=lambda x: -int(x.get("t", 0) or 0))

    nodes = []
    for n in analysis["graph_nodes"]:
        if str(n).startswith("IMEI:"):
            kind, color, size, group = "imei", "#8E44AD", 26, "Shared IMEI"
            label = str(n)
            title = str(n)
            ring = 2
        elif str(n) in targets:
            kind, color, size, group = "target", "#C0392B", 38, "Subject"
            label = fmt_msisdn(n)
            title = f"Subject / Target | {fmt_msisdn(n)}"
            ring = 0
        else:
            is_common = any(
                str(n) in (str(a), str(b)) and k == "common"
                for a, b, w, lab, k, *rest in analysis["graph_edges"]
            )
            if is_common:
                kind, color, size, group = "common", "#2980B9", 26, "Common contact"
                ring = 1
            else:
                kind, color, size, group = "exclusive", "#27AE60", 20, "Exclusive contact"
                ring = 2
            label = fmt_msisdn(n) if is_subscriber(n) else str(n)
            title = f"{group} | {label}"
        nodes.append(
            {
                "id": str(n),
                "label": label,
                "title": title,
                "group": group,
                "kind": kind,
                "ring": ring,
                "color": {
                    "background": color,
                    "border": "#1B2631",
                    "highlight": {"background": color, "border": "#000000"},
                    "hover": {"background": color, "border": "#000000"},
                },
                "size": size,
                "borderWidth": 2,
                "font": {
                    "color": "#1B2631",
                    "size": 12 if kind == "target" else 10,
                    "face": "Segoe UI, Tahoma, sans-serif",
                    "strokeWidth": 4,
                    "strokeColor": "#F7F4EF",
                    "vadjust": -32 if kind == "target" else -26,
                },
            }
        )

    # Clean shell positions: subjects center, common middle, exclusive/IMEI outer
    rings = {0: [], 1: [], 2: []}
    for n in nodes:
        rings[n["ring"]].append(n)
    radii = {0: 220, 1: 480, 2: 760}
    for ring, members in rings.items():
        members.sort(key=lambda x: x["id"])
        count = max(len(members), 1)
        phase = (ring * 0.35) + (0.08 if ring else 0)
        for i, n in enumerate(members):
            ang = phase + (2 * math.pi * i / count) - math.pi / 2
            n["x"] = radii[ring] * math.cos(ang)
            n["y"] = radii[ring] * math.sin(ang)
            n["fixed"] = False

    merged: dict[tuple[str, str], dict] = {}
    for edge in analysis["graph_edges"]:
        a, b, w, lab, kind = edge[0], edge[1], edge[2], edge[3], edge[4]
        dur = int(edge[5]) if len(edge) > 5 else 0
        key = tuple(sorted([str(a), str(b)]))
        color = {
            "target_link": "rgba(146,43,33,0.72)",
            "common": "rgba(36,113,163,0.55)",
            "imei": "rgba(108,52,131,0.55)",
        }.get(kind, "rgba(30,132,73,0.45)")
        highlight = {
            "target_link": "#922B21",
            "common": "#2471A3",
            "imei": "#6C3483",
        }.get(kind, "#1E8449")
        if key not in merged:
            ev = events_by_pair.get(key, [])
            # Prefer timeline aggregates when available
            if ev and kind != "imei":
                w = len(ev)
                dur = sum(int(x.get("d", 0) or 0) for x in ev)
            merged[key] = {
                "from": key[0],
                "to": key[1],
                "value": int(w),
                "duration": int(dur),
                "duration_label": fmt_duration(dur),
                "width": 1.0 + min(5.5, math.log1p(w) * 0.95),
                "title": f"{w} events | {fmt_duration(dur)}",
                "color": {
                    "color": color,
                    "highlight": highlight,
                    "hover": highlight,
                    "opacity": 0.7,
                },
                "kind": kind,
                "selectionWidth": 2,
                "hoverWidth": 1.4,
                "events": ev if kind != "imei" else [],
            }
        else:
            merged[key]["value"] += int(w)
            merged[key]["duration"] = int(merged[key].get("duration", 0)) + dur
            if kind != "imei" and key in events_by_pair and not merged[key].get("events"):
                merged[key]["events"] = events_by_pair[key]
            if merged[key].get("events"):
                merged[key]["value"] = len(merged[key]["events"])
                merged[key]["duration"] = sum(int(x.get("d", 0) or 0) for x in merged[key]["events"])
            merged[key]["duration_label"] = fmt_duration(merged[key]["duration"])
            merged[key]["width"] = 1.0 + min(5.5, math.log1p(merged[key]["value"]) * 0.95)
            merged[key]["title"] = f"{merged[key]['value']} events | {merged[key]['duration_label']}"

    edges = list(merged.values())
    edges.sort(key=lambda e: (-e["value"], e["from"], e["to"]))
    for i, e in enumerate(edges):
        step = 0.12 + (i % 8) * 0.045
        e["smooth"] = {
            "enabled": True,
            "type": "curvedCW" if i % 2 == 0 else "curvedCCW",
            "roundness": min(0.55, step),
        }
        show_label = e["kind"] == "target_link" or e["value"] >= 25 or e["duration"] >= 1800
        e["label"] = edge_label(e["value"], e["duration"]) if show_label else ""
        e["font"] = {
            "align": "middle",
            "size": 9,
            "color": "#2C3E50",
            "strokeWidth": 5,
            "strokeColor": "#F7F4EF",
            "face": "Segoe UI, Tahoma, sans-serif",
            "background": "rgba(247,244,239,0.92)",
        }

    total_duration = sum(int(e.get("duration", 0) or 0) for e in edges)
    if all_epochs:
        tmin = datetime.fromtimestamp(min(all_epochs))
        tmax = datetime.fromtimestamp(max(all_epochs))
    else:
        tmin = tmax = datetime.now()

    def to_local_input(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M")

    usage_counter = Counter()
    for rec in inv.records:
        usage_counter[rec.usage_class or "OTHER"] += 1
    usage_types = [
        {"id": k, "label": k, "count": v}
        for k, v in usage_counter.most_common()
    ]

    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "targets": [str(t) for t in analysis["targets"]],
        "nodes": nodes,
        "edges": edges,
        "time_range": {
            "min": to_local_input(tmin),
            "max": to_local_input(tmax),
            "min_epoch": int(tmin.timestamp()),
            "max_epoch": int(tmax.timestamp()),
        },
        "usage_types": usage_types,
        "stats": {
            "records": len(inv.records),
            "common_contacts": len(analysis["multi_common"]),
            "shared_cells": len(analysis["shared_cells"]),
            "targets": len(analysis["targets"]),
            "total_duration": total_duration,
            "total_duration_label": fmt_duration(total_duration),
        },
        "multi_common": [
            {
                "number": str(x["number"]),
                "targets": [str(t) for t in x["targets"]],
                "target_count": x["target_count"],
                "total_hits": x["total_hits"],
                "total_duration": x.get("total_duration", 0),
                "total_duration_label": fmt_duration(x.get("total_duration", 0)),
            }
            for x in analysis["multi_common"][:40]
        ],
    }


def safe_json_for_script(obj) -> str:
    """Embed JSON inside <script> without breaking HTML parsers."""
    return (
        json.dumps(obj, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>i2-style CDR Association Chart (Interactive)</title>
<script src="vendor/vis-network.min.js"></script>
<style>
  :root {
    --bg: #f7f4ef;
    --ink: #1b2631;
    --muted: #5d6d7e;
    --red: #c0392b;
    --blue: #2980b9;
    --green: #27ae60;
    --purple: #8e44ad;
    --line: #d5d8dc;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0;
    width: 100%;
    height: 100%;
    min-height: 100vh;
    background: var(--bg);
    color: var(--ink);
    font-family: "Segoe UI", Tahoma, sans-serif;
    overflow: hidden;
  }
  #app {
    display: grid;
    grid-template-columns: 300px 1fr;
    width: 100%;
    height: 100vh;
    min-height: 100vh;
  }
  aside {
    border-right: 1px solid var(--line);
    background: linear-gradient(180deg, #fff 0%, #f4f1ea 100%);
    padding: 14px;
    overflow: auto;
    z-index: 2;
  }
  h1 { font-size: 15px; margin: 0 0 4px; }
  .sub { font-size: 11px; color: var(--muted); margin-bottom: 12px; line-height: 1.35; }
  .stat {
    display: grid; grid-template-columns: 1fr auto;
    gap: 4px 8px; font-size: 12px;
    padding: 8px 10px; margin-bottom: 10px;
    background: #fff; border: 1px solid var(--line); border-radius: 8px;
  }
  .legend { display: grid; gap: 7px; margin: 10px 0 14px; font-size: 12px; }
  .legend div { display: flex; align-items: center; gap: 8px; }
  .dot { width: 12px; height: 12px; border-radius: 50%; border: 1px solid #1b2631; }
  .sq { border-radius: 3px; }
  .help {
    font-size: 11px; color: var(--muted); line-height: 1.45;
    border-top: 1px solid var(--line); padding-top: 10px; margin-top: 8px;
  }
  .help kbd {
    background: #eef1f4; border: 1px solid #cfd6dd; border-bottom-width: 2px;
    border-radius: 4px; padding: 0 4px; font-size: 10px;
  }
  .filters { display: grid; gap: 6px; margin: 8px 0 12px; font-size: 12px; }
  .filters label { display: flex; gap: 8px; align-items: center; cursor: pointer; }
  .timebox {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 8px 10px;
    margin-bottom: 10px;
  }
  .timebox h2 {
    font-size: 12px;
    margin: 0 0 6px;
    color: #1a5276;
  }
  .timebox .row {
    display: grid;
    gap: 4px;
    margin-bottom: 7px;
    font-size: 11px;
  }
  .timebox label { color: var(--muted); }
  .timebox input[type="datetime-local"] {
    width: 100%;
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 6px 7px;
    font-size: 11px;
    font-family: inherit;
  }
  .timebox .btns { display: flex; gap: 6px; }
  .timebox .btns button { flex: 1; padding: 6px 8px; font-size: 11px; }
  .timebox .rangehint {
    margin-top: 6px;
    font-size: 10px;
    color: var(--muted);
    line-height: 1.35;
  }
  #search {
    width: 100%;
    min-height: 72px;
    padding: 8px 10px;
    border: 1px solid var(--line);
    border-radius: 8px;
    font-size: 12px;
    font-family: inherit;
    resize: vertical;
    margin-bottom: 6px;
    line-height: 1.35;
  }
  .searchbox { margin-bottom: 10px; }
  .searchbox .shint {
    font-size: 10px;
    color: var(--muted);
    margin-bottom: 6px;
    line-height: 1.35;
  }
  .searchbox .srow { display: flex; gap: 6px; align-items: center; }
  .searchbox .srow button { flex: 0 0 auto; padding: 6px 8px; font-size: 11px; }
  #searchHit {
    flex: 1;
    font-size: 11px;
    color: #922b21;
    font-weight: 600;
  }
  #details {
    font-size: 11px; line-height: 1.4; background: #fff;
    border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px;
    min-height: 70px; white-space: pre-wrap;
  }
  #toolbar {
    position: absolute; top: 12px; right: 12px; z-index: 3;
    display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end;
  }
  button {
    border: 1px solid #aeb6bf; background: #fff; color: var(--ink);
    border-radius: 8px; padding: 7px 10px; font-size: 12px; cursor: pointer;
  }
  button:hover { background: #f2f4f6; }
  button.primary { background: #1b2631; color: #fff; border-color: #1b2631; }
  main { position: relative; min-width: 0; min-height: 0; height: 100%; }
  #network {
    width: 100%;
    height: 100%;
    min-height: 100%;
    background: var(--bg);
    cursor: grab;
  }
  #network:active { cursor: grabbing; }
  #hint {
    position: absolute; left: 12px; bottom: 10px; z-index: 3;
    font-size: 11px; color: var(--muted); background: #ffffffd0;
    border: 1px solid var(--line); border-radius: 8px; padding: 6px 9px;
  }
  #error {
    display: none;
    position: absolute; inset: 40px;
    z-index: 5;
    background: #fff5f5;
    border: 2px solid #c0392b;
    border-radius: 12px;
    padding: 18px;
    color: #922b21;
    white-space: pre-wrap;
    overflow: auto;
  }
  .leads { margin-top: 12px; }
  .leads h2 { font-size: 12px; margin: 0 0 6px; color: #922b21; }
  .lead {
    font-size: 11px; padding: 6px 7px; border-bottom: 1px solid #eee;
    cursor: pointer;
  }
  .lead:hover { background: #fdedec; }
  #edgeModal {
    display: none;
    position: fixed;
    inset: 0;
    z-index: 50;
    background: rgba(20, 28, 36, 0.45);
    align-items: center;
    justify-content: center;
    padding: 24px;
  }
  #edgeModal.open { display: flex; }
  .modal-card {
    width: min(1100px, 96vw);
    max-height: 88vh;
    background: #fff;
    border-radius: 12px;
    border: 1px solid #d5d8dc;
    box-shadow: 0 18px 50px rgba(0,0,0,.28);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .modal-head {
    display: flex;
    gap: 12px;
    align-items: flex-start;
    justify-content: space-between;
    padding: 14px 16px;
    border-bottom: 1px solid #e5e8eb;
    background: linear-gradient(180deg, #fff, #f7f4ef);
  }
  .modal-head h3 {
    margin: 0 0 4px;
    font-size: 15px;
  }
  .modal-head .meta {
    font-size: 11px;
    color: var(--muted);
    line-height: 1.4;
  }
  .modal-tools {
    display: flex;
    gap: 8px;
    align-items: center;
    padding: 10px 16px;
    border-bottom: 1px solid #eee;
    background: #fafafa;
  }
  .modal-tools input {
    flex: 1;
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 7px 9px;
    font-size: 12px;
  }
  .modal-body {
    overflow: auto;
    padding: 0;
  }
  .modal-body table {
    width: 100%;
    border-collapse: collapse;
    font-size: 11px;
  }
  .modal-body th {
    position: sticky;
    top: 0;
    background: #1b2631;
    color: #fff;
    text-align: left;
    padding: 8px 8px;
    white-space: nowrap;
  }
  .modal-body td {
    padding: 6px 8px;
    border-bottom: 1px solid #eef1f4;
    vertical-align: top;
    max-width: 220px;
    word-break: break-word;
  }
  .modal-body tr:nth-child(even) td { background: #fafbfc; }
  .modal-body tr:hover td { background: #fff8e8; }
  .badge {
    display: inline-block;
    padding: 1px 6px;
    border-radius: 999px;
    background: #eaf2f8;
    color: #1a5276;
    font-size: 10px;
  }
</style>
</head>
<body>
<div id="app">
  <aside>
    <h1>i2-style Association Chart</h1>
    <div class="sub">Occurrence CDR Cross-Match<br>Interactive — drag nodes / pan canvas / zoom</div>
    <div class="stat" id="stats">Loading…</div>
    <div class="timebox">
      <h2>Date / Time filter</h2>
      <div class="row">
        <label for="dtFrom">From</label>
        <input id="dtFrom" type="datetime-local" />
      </div>
      <div class="row">
        <label for="dtTo">To</label>
        <input id="dtTo" type="datetime-local" />
      </div>
      <div class="btns">
        <button id="btnApplyTime" class="primary" type="button">Apply</button>
        <button id="btnResetTime" type="button">Full range</button>
      </div>
      <div class="rangehint" id="timeHint">CDR full range</div>
    </div>
    <div class="timebox">
      <h2>Usage type filter</h2>
      <div class="filters" id="usageFilters" style="margin:0 0 8px;"></div>
      <div class="btns">
        <button id="btnUsageAll" type="button">All</button>
        <button id="btnUsageCalls" type="button">Calls only</button>
        <button id="btnUsageSms" type="button">SMS only</button>
      </div>
      <div class="rangehint" id="usageHint">All usage types</div>
    </div>
    <div class="searchbox">
      <div class="shint">Multi number search — paste many MSISDNs (comma / space / new line). All nodes stay visible; matches highlight.</div>
      <textarea id="search" placeholder="e.g.&#10;8801614033111&#10;01794935733&#10;8801627079026"></textarea>
      <div class="srow">
        <button id="btnClearSearch" type="button">Clear</button>
        <span id="searchHit">No search</span>
      </div>
    </div>
    <div class="filters">
      <label><input type="checkbox" id="f_target" checked> Subjects (red)</label>
      <label><input type="checkbox" id="f_common" checked> Common contacts (blue)</label>
      <label><input type="checkbox" id="f_exclusive" checked> Exclusive contacts (green)</label>
      <label><input type="checkbox" id="f_imei" checked> Shared IMEI (purple)</label>
    </div>
    <div class="legend">
      <div><span class="dot" style="background:var(--red)"></span>Subject (A-Party / Target)</div>
      <div><span class="dot" style="background:var(--blue)"></span>Common contact (2+ targets)</div>
      <div><span class="dot" style="background:var(--green)"></span>Strong exclusive contact</div>
      <div><span class="dot sq" style="background:var(--purple)"></span>Shared IMEI / Handset</div>
    </div>
    <div id="details">Click a node or link for details.</div>
    <div class="leads">
      <h2>Priority common leads</h2>
      <div id="leads"></div>
    </div>
    <div class="help">
      <div><kbd>Drag node</kbd> move entity</div>
      <div><kbd>Drag background</kbd> pan chart</div>
      <div><kbd>Scroll</kbd> zoom in/out</div>
      <div><kbd>Double-click link</kbd> open CDR log modal</div>
      <div><kbd>Multi search</kbd> highlight gold nodes</div>
      <div><kbd>Double-click node</kbd> focus & neighbors</div>
    </div>
  </aside>
  <main>
    <div id="toolbar">
      <button id="btnFit" class="primary">Fit view</button>
      <button id="btnLabels">Labels: KEY</button>
      <button id="btnPhysics">Physics: OFF</button>
      <button id="btnStabilize">Re-space</button>
      <button id="btnReset">Reset layout</button>
    </div>
    <div id="network"></div>
    <div id="error"></div>
    <div id="hint">Double-click a link to open CDR logs · date/time filter · multi search highlight · Generated: __GENERATED__</div>
  </main>
</div>
<div id="edgeModal" aria-hidden="true">
  <div class="modal-card" role="dialog" aria-modal="true">
    <div class="modal-head">
      <div>
        <h3 id="modalTitle">Edge CDR logs</h3>
        <div class="meta" id="modalMeta"></div>
      </div>
      <button id="btnCloseModal" type="button">Close</button>
    </div>
    <div class="modal-tools">
      <input id="modalFilter" type="search" placeholder="Filter logs in this link…" />
      <button id="btnExportCsv" type="button">Export CSV</button>
    </div>
    <div class="modal-body">
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Date Time</th>
            <th>Duration</th>
            <th>Usage</th>
            <th>A-Party</th>
            <th>B-Party</th>
            <th>Network</th>
            <th>Provider</th>
            <th>IMEI</th>
            <th>LAC/CI</th>
            <th>Address</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody id="modalRows"></tbody>
      </table>
    </div>
  </div>
</div>
<script id="graph-data" type="application/json">
__DATA__
</script>
<script>
(function () {
  function showError(msg) {
    var el = document.getElementById('error');
    el.style.display = 'block';
    el.textContent = msg;
    console.error(msg);
  }

  try {
    if (typeof vis === 'undefined' || !vis.Network || !vis.DataSet) {
      showError(
        'vis-network library failed to load.\n\n' +
        'Open this file from the output folder (not by copy-paste).\n' +
        'Required file must exist:\n' +
        '  output/vendor/vis-network.min.js\n\n' +
        'Or re-run: python export_i2_html.py'
      );
      return;
    }

    var raw = document.getElementById('graph-data').textContent;
    var DATA = JSON.parse(raw);
    if (!DATA.nodes || !DATA.nodes.length) {
      showError('Graph data is empty.');
      return;
    }

    var timeRange = DATA.time_range || {};
    var dtFromEl = document.getElementById('dtFrom');
    var dtToEl = document.getElementById('dtTo');
    var timeHintEl = document.getElementById('timeHint');
    if (timeRange.min) {
      dtFromEl.value = timeRange.min;
      dtFromEl.min = timeRange.min;
      dtFromEl.max = timeRange.max;
    }
    if (timeRange.max) {
      dtToEl.value = timeRange.max;
      dtToEl.min = timeRange.min;
      dtToEl.max = timeRange.max;
    }
    timeHintEl.textContent = 'Full CDR range: ' + (timeRange.min || '?') + ' → ' + (timeRange.max || '?');

    // Usage type checkboxes
    var usageBox = document.getElementById('usageFilters');
    var usageHintEl = document.getElementById('usageHint');
    var usageTypes = DATA.usage_types || [];
    if (!usageTypes.length) {
      usageTypes = [
        { id: 'CALL_MO', label: 'CALL_MO', count: 0 },
        { id: 'CALL_MT', label: 'CALL_MT', count: 0 },
        { id: 'SMSMO', label: 'SMSMO', count: 0 },
        { id: 'SMSMT', label: 'SMSMT', count: 0 }
      ];
    }
    usageBox.innerHTML = usageTypes.map(function (u) {
      var id = 'usage_' + String(u.id).replace(/[^A-Za-z0-9_]/g, '_');
      return '<label><input type="checkbox" class="usageChk" id="' + id + '" data-usage="' +
        String(u.id).replace(/"/g, '&quot;') + '" checked> ' +
        u.label + ' <span style="color:#8a9199">(' + Number(u.count || 0).toLocaleString() + ')</span></label>';
    }).join('');

    function getSelectedUsage() {
      var selected = {};
      var any = false;
      var boxes = document.querySelectorAll('.usageChk');
      boxes.forEach(function (el) {
        if (el.checked) {
          selected[el.getAttribute('data-usage')] = true;
          any = true;
        }
      });
      return { map: selected, any: any, all: any && boxes.length === Object.keys(selected).length };
    }

    function setUsageSelection(mode) {
      document.querySelectorAll('.usageChk').forEach(function (el) {
        var u = el.getAttribute('data-usage') || '';
        if (mode === 'all') el.checked = true;
        else if (mode === 'calls') el.checked = u.indexOf('CALL') === 0 || u === 'MOC' || u === 'MTC';
        else if (mode === 'sms') el.checked = u.indexOf('SMS') === 0;
      });
      applyFilters(false);
    }

    function eventUsageClass(ev) {
      if (!ev || typeof ev !== 'object') return 'OTHER';
      return ev.usage_class || ev.usage || 'OTHER';
    }

    function updateStats(extra) {
      extra = extra || {};
      document.getElementById('stats').innerHTML =
        '<span>Subjects</span><b>' + DATA.stats.targets + '</b>' +
        '<span>CDR events</span><b>' + Number(DATA.stats.records).toLocaleString() + '</b>' +
        '<span>Visible links</span><b>' + (extra.links != null ? extra.links : DATA.edges.length) + '</b>' +
        '<span>Visible duration</span><b>' + (extra.durationLabel || DATA.stats.total_duration_label || '0s') + '</b>' +
        '<span>Visible nodes</span><b>' + (extra.nodes != null ? extra.nodes : DATA.nodes.length) + '</b>' +
        '<span>Window events</span><b>' + (extra.events != null ? extra.events : 'all') + '</b>';
    }
    updateStats();

    function fmt(num) {
      var n = String(num || '');
      if (n.indexOf('880') === 0 && n.length === 13) {
        return '+' + n.slice(0,3) + ' ' + n.slice(3,5) + ' ' + n.slice(5,9) + ' ' + n.slice(9);
      }
      return n;
    }

    function fmtDur(sec) {
      sec = Number(sec || 0);
      if (sec <= 0) return '0s';
      var h = Math.floor(sec / 3600);
      var m = Math.floor((sec % 3600) / 60);
      var s = sec % 60;
      if (h) return h + 'h' + String(m).padStart(2, '0') + 'm' + String(s).padStart(2, '0') + 's';
      if (m) return m + 'm' + String(s).padStart(2, '0') + 's';
      return s + 's';
    }

    function parseLocalDateTime(val) {
      if (!val) return null;
      // datetime-local -> local Date
      var d = new Date(val);
      if (isNaN(d.getTime())) return null;
      return Math.floor(d.getTime() / 1000);
    }

    function getTimeWindow() {
      var fromSec = parseLocalDateTime(dtFromEl.value);
      var toSec = parseLocalDateTime(dtToEl.value);
      if (fromSec == null) fromSec = timeRange.min_epoch || 0;
      if (toSec == null) toSec = timeRange.max_epoch || 2147483647;
      // include the whole selected minute on the "to" side
      if (dtToEl.value && dtToEl.value.length <= 16) toSec += 59;
      if (fromSec > toSec) {
        var tmp = fromSec; fromSec = toSec; toSec = tmp;
      }
      return { from: fromSec, to: toSec };
    }

    function eventTime(ev) {
      if (ev == null) return 0;
      if (typeof ev === 'object' && !Array.isArray(ev)) return Number(ev.t || 0);
      return Number(ev[0] || 0);
    }
    function eventDur(ev) {
      if (ev == null) return 0;
      if (typeof ev === 'object' && !Array.isArray(ev)) return Number(ev.d || 0);
      return Number(ev[1] || 0);
    }

    function edgeInWindow(e, win, usageSel) {
      usageSel = usageSel || getSelectedUsage();
      if (e.kind === 'imei') {
        return {
          ok: true,
          value: e.value || 0,
          duration: e.duration || 0,
          duration_label: e.duration_label || fmtDur(e.duration || 0),
          logs: []
        };
      }
      var ev = e.events || [];
      if (!ev.length) {
        var full = (!timeRange.min_epoch || win.from <= timeRange.min_epoch) &&
                   (!timeRange.max_epoch || win.to >= timeRange.max_epoch);
        return {
          ok: full && (e.value || 0) > 0 && usageSel.any,
          value: e.value || 0,
          duration: e.duration || 0,
          duration_label: e.duration_label || fmtDur(e.duration || 0),
          logs: []
        };
      }
      var count = 0;
      var dur = 0;
      var logs = [];
      for (var i = 0; i < ev.length; i++) {
        var t = eventTime(ev[i]);
        if (t < win.from || t > win.to) continue;
        var uc = eventUsageClass(ev[i]);
        if (!usageSel.map[uc]) continue;
        count += 1;
        dur += eventDur(ev[i]);
        logs.push(ev[i]);
      }
      return {
        ok: count > 0,
        value: count,
        duration: dur,
        duration_label: fmtDur(dur),
        logs: logs
      };
    }

    var leadsEl = document.getElementById('leads');
    var leads = DATA.multi_common || [];
    leadsEl.innerHTML = leads.slice(0, 12).map(function (item) {
      return '<div class="lead" data-id="' + item.number + '"><b>' + fmt(item.number) +
        '</b><br>' + item.target_count + ' targets · ' + item.total_hits + ' hits · ' +
        (item.total_duration_label || fmtDur(item.total_duration)) + '</div>';
    }).join('') || '<div class="lead">No common leads</div>';

    var container = document.getElementById('network');
    // Force measurable size before init (fixes blank canvas)
    container.style.width = '100%';
    container.style.height = '100%';

    var rawNodes = DATA.nodes.slice();
    var rawEdges = DATA.edges.slice();

    function toVisNodes(list) {
      return list.map(function (n) {
        var copy = Object.assign({}, n);
        copy.shape = (n.kind === 'imei') ? 'box' : 'dot';
        // keep precomputed shell coordinates
        if (typeof n.x === 'number') copy.x = n.x;
        if (typeof n.y === 'number') copy.y = n.y;
        return copy;
      });
    }

    function decorateEdge(e, i, labelMode, metrics) {
      var copy = Object.assign({ id: i + 1 }, e);
      var value = metrics ? metrics.value : (e.value || 0);
      var duration = metrics ? metrics.duration : (e.duration || 0);
      var durationLabel = metrics ? metrics.duration_label : (e.duration_label || fmtDur(duration));
      copy.value = value;
      copy.duration = duration;
      copy.duration_label = durationLabel;
      copy.width = 1.0 + Math.min(5.5, Math.log1p(value) * 0.95);
      copy.title = value + ' events | ' + durationLabel;
      var show = false;
      if (labelMode === 'all') show = true;
      else if (labelMode === 'key') {
        show = e.kind === 'target_link' || value >= 25 || duration >= 1800;
      }
      copy.label = show ? (value + ' | ' + durationLabel) : '';
      // keep events for later refilters; vis ignores unknown fields fine
      return copy;
    }

    var labelMode = 'key'; // none | key | all
    var nodesDS = new vis.DataSet(toVisNodes(rawNodes));
    var edgesDS = new vis.DataSet(rawEdges.map(function (e, i) {
      return decorateEdge(e, i, labelMode, null);
    }));

    var physicsOn = false;
    var options = {
      autoResize: true,
      height: '100%',
      width: '100%',
      interaction: {
        hover: true,
        tooltipDelay: 80,
        dragNodes: true,
        dragView: true,
        zoomView: true,
        navigationButtons: true,
        keyboard: { enabled: true, bindToWindow: false },
        multiselect: true,
        hideEdgesOnDrag: false,
        hideEdgesOnZoom: false
      },
      layout: {
        improvedLayout: false,
        randomSeed: 42
      },
      physics: {
        enabled: false,
        solver: 'repulsion',
        repulsion: {
          centralGravity: 0.0,
          springLength: 220,
          springConstant: 0.02,
          nodeDistance: 170,
          damping: 0.18
        },
        stabilization: { enabled: true, iterations: 120, updateInterval: 25, fit: true }
      },
      edges: {
        arrows: { to: { enabled: false } },
        selectionWidth: 2.2,
        hoverWidth: 1.6,
        smooth: { enabled: true, type: 'curvedCW', roundness: 0.22 },
        chosen: true
      },
      nodes: {
        margin: 10,
        shadow: { enabled: true, color: 'rgba(0,0,0,0.12)', size: 5, x: 1, y: 1 },
        scaling: { min: 14, max: 42 }
      }
    };

    var network = new vis.Network(container, { nodes: nodesDS, edges: edgesDS }, options);

    // Short soft settle without collapsing the shell layout
    network.once('afterDrawing', function firstDraw() {
      network.off('afterDrawing', firstDraw);
      network.fit({ animation: false, padding: 40 });
    });

    setTimeout(function () {
      network.redraw();
      network.fit({ padding: 40 });
    }, 200);

    window.addEventListener('resize', function () {
      network.redraw();
    });

    function setDetails(text) {
      document.getElementById('details').textContent = text;
    }

    // Dim non-neighbor edges on select for readability (skip when multi-search highlight active)
    network.on('selectNode', function (params) {
      if (!params.nodes.length) return;
      if (getSearchQueries().length) return; // keep multi-search highlight intact
      var id = params.nodes[0];
      var keep = {};
      keep[id] = true;
      network.getConnectedNodes(id).forEach(function (n) { keep[n] = true; });
      var edgeKeep = {};
      network.getConnectedEdges(id).forEach(function (e) { edgeKeep[e] = true; });
      var nodeUpdates = nodesDS.getIds().map(function (nid) {
        var n = nodesDS.get(nid);
        return {
          id: nid,
          opacity: keep[nid] ? 1 : 0.18,
          font: Object.assign({}, n.font || {}, { color: keep[nid] ? '#1B2631' : '#AEB6BF' })
        };
      });
      var edgeUpdates = edgesDS.getIds().map(function (eid) {
        var e = edgesDS.get(eid);
        var active = !!edgeKeep[eid];
        return {
          id: eid,
          color: {
            color: active ? (e.kind === 'target_link' ? '#922B21' : (e.kind === 'common' ? '#2471A3' : '#1E8449')) : 'rgba(180,180,180,0.18)',
            highlight: e.kind === 'target_link' ? '#922B21' : '#2471A3',
            hover: e.kind === 'target_link' ? '#922B21' : '#2471A3',
            opacity: active ? 0.95 : 0.15
          },
          width: active ? Math.max(2, e.width || 1.5) : 0.7,
          label: active ? ((e.value || 0) + ' | ' + (e.duration_label || fmtDur(e.duration))) : ''
        };
      });
      nodesDS.update(nodeUpdates);
      edgesDS.update(edgeUpdates);
    });

    network.on('deselectNode', function () {
      applyFilters(true);
    });
    network.on('deselectEdge', function () {
      applyFilters(true);
    });

    network.on('click', function (params) {
      if (params.nodes.length) {
        var id = params.nodes[0];
        var node = nodesDS.get(id);
        var connected = network.getConnectedNodes(id);
        var edgeIds = network.getConnectedEdges(id);
        var total = 0;
        var totalDur = 0;
        edgeIds.forEach(function (eid) {
          var ed = edgesDS.get(eid) || {};
          total += Number(ed.value || 0);
          totalDur += Number(ed.duration || 0);
        });
        setDetails(
          (node.group || '') + '\n' + (node.label || '') + '\nID: ' + id +
          '\nLinks: ' + connected.length +
          '\nEvents on links: ' + total +
          '\nCall duration on links: ' + fmtDur(totalDur)
        );
      } else if (params.edges.length) {
        var e = edgesDS.get(params.edges[0]);
        var a = nodesDS.get(e.from);
        var b = nodesDS.get(e.to);
        setDetails(
          'Link\n' + a.label + ' <-> ' + b.label +
          '\nEvents: ' + e.value +
          '\nCall duration: ' + (e.duration_label || fmtDur(e.duration)) +
          ' (' + Number(e.duration || 0) + ' sec)' +
          '\nType: ' + (e.kind || 'contact')
        );
      } else {
        setDetails('Click a node or link for details.');
      }
    });

    network.on('doubleClick', function (params) {
      if (params.edges && params.edges.length) {
        openEdgeModal(params.edges[0]);
        return;
      }
      if (!params.nodes.length) return;
      var id = params.nodes[0];
      var neigh = network.getConnectedNodes(id).concat([id]);
      network.selectNodes(neigh);
      network.focus(id, { scale: 1.25, animation: true });
    });

    var modalState = { logs: [], edge: null };

    function escHtml(s) {
      return String(s == null ? '' : s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }

    function openEdgeModal(edgeId) {
      var e = edgesDS.get(edgeId);
      if (!e) return;
      var a = nodesDS.get(e.from) || { label: e.from, id: e.from };
      var b = nodesDS.get(e.to) || { label: e.to, id: e.to };
      var win = getTimeWindow();
      var usageSel = getSelectedUsage();
      var metrics = edgeInWindow(e, win, usageSel);
      var logs = (metrics.logs && metrics.logs.length)
        ? metrics.logs.slice()
        : (e.events || []).slice();
      logs.sort(function (x, y) { return eventTime(y) - eventTime(x); });
      modalState.logs = logs;
      modalState.edge = e;

      document.getElementById('modalTitle').textContent =
        (a.label || e.from) + '  ↔  ' + (b.label || e.to);
      document.getElementById('modalMeta').innerHTML =
        'Type: <span class="badge">' + escHtml(e.kind || 'contact') + '</span> · ' +
        logs.length + ' CDR logs in current date/usage filter · total duration ' +
        escHtml(metrics.duration_label || fmtDur(metrics.duration)) +
        '<br>IDs: ' + escHtml(e.from) + ' ↔ ' + escHtml(e.to) +
        '<br>Usage filter: ' + escHtml(usageSel.any ? Object.keys(usageSel.map).join(', ') : 'none');
      document.getElementById('modalFilter').value = '';
      renderModalRows('');
      document.getElementById('edgeModal').classList.add('open');
      document.getElementById('edgeModal').setAttribute('aria-hidden', 'false');
    }

    function renderModalRows(q) {
      q = (q || '').toLowerCase().trim();
      var rows = modalState.logs.filter(function (log) {
        if (!q) return true;
        var hay = [
          log.dt, log.usage, log.usage_class, log.aparty, log.bparty, log.network,
          log.provider, log.imei, log.lac, log.ci, log.address, log.source, log.target
        ].join(' ').toLowerCase();
        return hay.indexOf(q) >= 0;
      });
      var body = document.getElementById('modalRows');
      if (!rows.length) {
        body.innerHTML = '<tr><td colspan="12" style="padding:18px;color:#7f8c8d;">No CDR logs for this link in the selected date/time window.</td></tr>';
        return;
      }
      body.innerHTML = rows.map(function (log, idx) {
        var dt = log.dt || (eventTime(log) ? new Date(eventTime(log) * 1000).toLocaleString() : '-');
        var lacci = ((log.lac || '-') + ' / ' + (log.ci || '-'));
        return '<tr>' +
          '<td>' + (idx + 1) + '</td>' +
          '<td>' + escHtml(dt) + '</td>' +
          '<td>' + escHtml(fmtDur(eventDur(log))) + '</td>' +
          '<td>' + escHtml(log.usage || log.usage_class || '-') + '</td>' +
          '<td>' + escHtml(log.aparty || '-') + '</td>' +
          '<td>' + escHtml(log.bparty || '-') + '</td>' +
          '<td>' + escHtml(log.network || '-') + '</td>' +
          '<td>' + escHtml(log.provider || '-') + '</td>' +
          '<td>' + escHtml(log.imei || '-') + '</td>' +
          '<td>' + escHtml(lacci) + '</td>' +
          '<td>' + escHtml(log.address || '-') + '</td>' +
          '<td>' + escHtml(log.source || '-') + '</td>' +
          '</tr>';
      }).join('');
    }

    function closeEdgeModal() {
      document.getElementById('edgeModal').classList.remove('open');
      document.getElementById('edgeModal').setAttribute('aria-hidden', 'true');
    }

    function exportEdgeCsv() {
      var logs = modalState.logs || [];
      var header = ['datetime','duration_sec','usage','aparty','bparty','network','provider','imei','imsi','lac','ci','address','target','source'];
      var lines = [header.join(',')];
      logs.forEach(function (log) {
        var row = [
          log.dt || '',
          eventDur(log),
          log.usage || log.usage_class || '',
          log.aparty || '',
          log.bparty || '',
          log.network || '',
          log.provider || '',
          log.imei || '',
          log.imsi || '',
          log.lac || '',
          log.ci || '',
          log.address || '',
          log.target || '',
          log.source || ''
        ].map(function (v) {
          var s = String(v).replace(/"/g, '""');
          return '"' + s + '"';
        });
        lines.push(row.join(','));
      });
      var blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      var e = modalState.edge || {};
      a.href = url;
      a.download = 'cdr_edge_' + (e.from || 'a') + '_' + (e.to || 'b') + '.csv';
      a.click();
      URL.revokeObjectURL(url);
    }

    document.getElementById('btnCloseModal').onclick = closeEdgeModal;
    document.getElementById('edgeModal').addEventListener('click', function (ev) {
      if (ev.target.id === 'edgeModal') closeEdgeModal();
    });
    document.getElementById('modalFilter').addEventListener('input', function (ev) {
      renderModalRows(ev.target.value);
    });
    document.getElementById('btnExportCsv').onclick = exportEdgeCsv;
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') closeEdgeModal();
    });

    function normalizeMsisdnDigits(v) {
      var d = String(v || '').replace(/\D/g, '');
      if (!d) return '';
      if (d.length === 11 && d.charAt(0) === '0') d = '88' + d; // 01xxxxxxxxx -> 8801...
      if (d.length === 10 && d.charAt(0) === '1') d = '880' + d;
      return d;
    }

    function getSearchQueries() {
      var raw = document.getElementById('search').value || '';
      return raw
        .split(/[\s,;|/]+/)
        .map(function (x) { return x.trim(); })
        .filter(Boolean);
    }

    function nodeMatchesQueries(node, queries) {
      if (!queries.length) return false;
      var id = String(node.id || '');
      var label = String(node.label || '');
      var idDigits = normalizeMsisdnDigits(id);
      var labelDigits = normalizeMsisdnDigits(label);
      for (var i = 0; i < queries.length; i++) {
        var q = queries[i];
        var qLow = q.toLowerCase();
        var qDigits = normalizeMsisdnDigits(q);
        if (id.toLowerCase().indexOf(qLow) >= 0 || label.toLowerCase().indexOf(qLow) >= 0) return true;
        if (qDigits && (idDigits === qDigits || idDigits.indexOf(qDigits) >= 0 || qDigits.indexOf(idDigits) >= 0 && idDigits.length >= 10)) return true;
        if (qDigits && labelDigits && (labelDigits === qDigits || labelDigits.indexOf(qDigits) >= 0)) return true;
      }
      return false;
    }

    function baseNodeStyle(n) {
      var src = null;
      for (var i = 0; i < rawNodes.length; i++) {
        if (rawNodes[i].id === n.id) { src = rawNodes[i]; break; }
      }
      src = src || n;
      return {
        id: n.id,
        size: src.size,
        borderWidth: src.borderWidth || 2,
        color: src.color,
        opacity: 1,
        font: Object.assign({}, src.font || {}),
        shadow: { enabled: true, color: 'rgba(0,0,0,0.12)', size: 5, x: 1, y: 1 }
      };
    }

    function applySearchHighlight() {
      var queries = getSearchQueries();
      var hitEl = document.getElementById('searchHit');
      var ids = nodesDS.getIds();
      if (!queries.length) {
        hitEl.textContent = 'No search';
        var resetNodes = ids.map(function (id) { return baseNodeStyle(nodesDS.get(id)); });
        nodesDS.update(resetNodes);
        // restore edge colors lightly from current metrics already in edgesDS
        var resetEdges = edgesDS.getIds().map(function (eid) {
          var e = edgesDS.get(eid);
          var kindColor = e.kind === 'target_link' ? 'rgba(146,43,33,0.72)'
            : e.kind === 'common' ? 'rgba(36,113,163,0.55)'
            : e.kind === 'imei' ? 'rgba(108,52,131,0.55)'
            : 'rgba(30,132,73,0.45)';
          return {
            id: eid,
            color: {
              color: kindColor,
              highlight: e.kind === 'target_link' ? '#922B21' : '#2471A3',
              hover: e.kind === 'target_link' ? '#922B21' : '#2471A3',
              opacity: 0.7
            },
            width: e.width
          };
        });
        edgesDS.update(resetEdges);
        return [];
      }

      var matched = [];
      var matchedSet = {};
      ids.forEach(function (id) {
        var n = nodesDS.get(id);
        if (nodeMatchesQueries(n, queries)) {
          matched.push(id);
          matchedSet[id] = true;
        }
      });

      var nodeUpdates = ids.map(function (id) {
        var n = nodesDS.get(id);
        var base = baseNodeStyle(n);
        if (matchedSet[id]) {
          return {
            id: id,
            size: (base.size || 24) + 14,
            borderWidth: 5,
            color: {
              background: (base.color && base.color.background) || '#C0392B',
              border: '#F1C40F',
              highlight: { background: (base.color && base.color.background) || '#C0392B', border: '#F39C12' },
              hover: { background: (base.color && base.color.background) || '#C0392B', border: '#F39C12' }
            },
            opacity: 1,
            font: Object.assign({}, base.font, {
              color: '#7D6608',
              size: (base.font && base.font.size ? base.font.size : 11) + 2,
              strokeWidth: 5,
              strokeColor: '#FFF8DC',
              bold: true
            }),
            shadow: { enabled: true, color: 'rgba(241,196,15,0.85)', size: 18, x: 0, y: 0 }
          };
        }
        return {
          id: id,
          size: base.size,
          borderWidth: base.borderWidth,
          color: base.color,
          opacity: 0.28,
          font: Object.assign({}, base.font, { color: '#AAB0B6', strokeWidth: 2 }),
          shadow: { enabled: false }
        };
      });
      nodesDS.update(nodeUpdates);

      var edgeUpdates = edgesDS.getIds().map(function (eid) {
        var e = edgesDS.get(eid);
        var both = matchedSet[e.from] && matchedSet[e.to];
        var one = matchedSet[e.from] || matchedSet[e.to];
        if (both) {
          return {
            id: eid,
            color: { color: '#F39C12', highlight: '#D68910', hover: '#D68910', opacity: 1 },
            width: Math.max(3.5, (e.width || 1) + 2),
            label: (e.value || 0) + ' | ' + (e.duration_label || fmtDur(e.duration))
          };
        }
        if (one) {
          return {
            id: eid,
            color: { color: 'rgba(243,156,18,0.55)', highlight: '#F39C12', hover: '#F39C12', opacity: 0.85 },
            width: Math.max(2, e.width || 1.5)
          };
        }
        return {
          id: eid,
          color: { color: 'rgba(180,180,180,0.18)', highlight: '#B0B0B0', hover: '#B0B0B0', opacity: 0.15 },
          width: 0.7,
          label: ''
        };
      });
      edgesDS.update(edgeUpdates);

      hitEl.textContent = matched.length
        ? (matched.length + ' hit' + (matched.length > 1 ? 's' : '') + ' / ' + queries.length + ' quer' + (queries.length > 1 ? 'ies' : 'y'))
        : ('0 hits / ' + queries.length + ' quer' + (queries.length > 1 ? 'ies' : 'y'));

      if (matched.length) {
        try {
          network.selectNodes(matched);
          if (matched.length === 1) {
            network.focus(matched[0], { scale: 1.2, animation: true });
          } else {
            network.fit({ nodes: matched, animation: true, padding: 60 });
          }
        } catch (err) {}
      }
      return matched;
    }

    function applyFilters(keepView) {
      var show = {
        target: document.getElementById('f_target').checked,
        common: document.getElementById('f_common').checked,
        exclusive: document.getElementById('f_exclusive').checked,
        imei: document.getElementById('f_imei').checked
      };
      var win = getTimeWindow();
      var usageSel = getSelectedUsage();

      // First pass: edges that survive date + usage filter
      var timedEdges = [];
      var activeNodes = {};
      var windowEvents = 0;
      var windowDuration = 0;
      DATA.targets.forEach(function (t) { activeNodes[t] = true; });

      rawEdges.forEach(function (e) {
        var metrics = edgeInWindow(e, win, usageSel);
        if (!metrics.ok) return;
        timedEdges.push({ e: e, metrics: metrics });
        if (e.kind !== 'imei') {
          activeNodes[e.from] = true;
          activeNodes[e.to] = true;
          windowEvents += metrics.value;
          windowDuration += metrics.duration;
        }
      });

      var visibleIds = {};
      rawNodes.forEach(function (n) {
        if (!show[n.kind]) return;
        if (n.kind === 'target') {
          visibleIds[n.id] = true;
          return;
        }
        if (n.kind === 'imei') return; // decided with edges
        if (activeNodes[n.id]) visibleIds[n.id] = true;
      });

      var finalEdges = [];
      timedEdges.forEach(function (item) {
        var e = item.e;
        if (e.kind === 'imei') {
          if (!show.imei) return;
          // show IMEI if linked subject is visible
          var other = String(e.from).indexOf('IMEI:') === 0 ? e.to : e.from;
          var imei = String(e.from).indexOf('IMEI:') === 0 ? e.from : e.to;
          if (visibleIds[other]) {
            visibleIds[imei] = true;
            finalEdges.push(item);
          }
          return;
        }
        if (!visibleIds[e.from] || !visibleIds[e.to]) return;
        finalEdges.push(item);
      });

      nodesDS.clear();
      nodesDS.add(toVisNodes(rawNodes.filter(function (n) { return visibleIds[n.id]; })));
      var ei = 0;
      edgesDS.clear();
      edgesDS.add(finalEdges.map(function (item) {
        return decorateEdge(item.e, ei++, labelMode, item.metrics);
      }));

      updateStats({
        links: finalEdges.length,
        nodes: Object.keys(visibleIds).length,
        events: windowEvents,
        durationLabel: fmtDur(windowDuration)
      });
      timeHintEl.textContent =
        'Filter: ' + (dtFromEl.value || timeRange.min) + ' → ' + (dtToEl.value || timeRange.max) +
        ' · ' + windowEvents + ' events · ' + fmtDur(windowDuration);
      var selectedUsage = Object.keys(usageSel.map);
      usageHintEl.textContent = !usageSel.any
        ? 'No usage type selected'
        : (usageSel.all ? 'All usage types' : ('Usage: ' + selectedUsage.join(', ')));

      applySearchHighlight();

      if (!keepView && !getSearchQueries().length) network.fit({ animation: true, padding: 40 });
    }

    ['f_target', 'f_common', 'f_exclusive', 'f_imei'].forEach(function (id) {
      document.getElementById(id).addEventListener('change', function () { applyFilters(false); });
    });
    document.querySelectorAll('.usageChk').forEach(function (el) {
      el.addEventListener('change', function () { applyFilters(false); });
    });
    document.getElementById('btnUsageAll').onclick = function () { setUsageSelection('all'); };
    document.getElementById('btnUsageCalls').onclick = function () { setUsageSelection('calls'); };
    document.getElementById('btnUsageSms').onclick = function () { setUsageSelection('sms'); };
    var searchTimer = null;
    document.getElementById('search').addEventListener('input', function () {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(function () { applySearchHighlight(); }, 180);
    });
    document.getElementById('btnClearSearch').onclick = function () {
      document.getElementById('search').value = '';
      applyFilters(true);
    };
    document.getElementById('btnApplyTime').onclick = function () { applyFilters(false); };
    document.getElementById('btnResetTime').onclick = function () {
      if (timeRange.min) dtFromEl.value = timeRange.min;
      if (timeRange.max) dtToEl.value = timeRange.max;
      applyFilters(false);
    };
    dtFromEl.addEventListener('change', function () { applyFilters(false); });
    dtToEl.addEventListener('change', function () { applyFilters(false); });

    document.getElementById('btnFit').onclick = function () {
      network.fit({ animation: true, padding: 40 });
    };
    document.getElementById('btnLabels').onclick = function () {
      labelMode = (labelMode === 'key') ? 'all' : (labelMode === 'all' ? 'none' : 'key');
      document.getElementById('btnLabels').textContent =
        'Labels: ' + (labelMode === 'key' ? 'KEY' : labelMode.toUpperCase());
      applyFilters(true);
    };
    document.getElementById('btnStabilize').onclick = function () {
      network.setOptions({ physics: { enabled: true } });
      network.stabilize(80);
      setTimeout(function () {
        network.setOptions({ physics: { enabled: physicsOn } });
        network.fit({ padding: 40 });
      }, 700);
    };
    document.getElementById('btnPhysics').onclick = function () {
      physicsOn = !physicsOn;
      network.setOptions({ physics: { enabled: physicsOn } });
      document.getElementById('btnPhysics').textContent = 'Physics: ' + (physicsOn ? 'ON' : 'OFF');
    };
    document.getElementById('btnReset').onclick = function () {
      labelMode = 'key';
      physicsOn = false;
      document.getElementById('btnLabels').textContent = 'Labels: KEY';
      document.getElementById('btnPhysics').textContent = 'Physics: OFF';
      if (timeRange.min) dtFromEl.value = timeRange.min;
      if (timeRange.max) dtToEl.value = timeRange.max;
      network.setOptions({ physics: { enabled: false } });
      applyFilters(false);
    };

    // Apply once so date window + stats sync
    applyFilters(true);

    leadsEl.addEventListener('click', function (ev) {
      var el = ev.target.closest('.lead');
      if (!el) return;
      var id = el.getAttribute('data-id');
      if (!nodesDS.get(id)) {
        setDetails('Lead ' + id + ' is filtered out or not in graph. Enable Common contacts.');
        return;
      }
      network.selectNodes([id]);
      network.focus(id, { scale: 1.4, animation: true });
    });
  } catch (err) {
    showError('Chart failed to start:\n' + (err && err.stack ? err.stack : err));
  }
})();
</script>
</body>
</html>
"""


def main():
    ensure_vis_bundle()
    print("Loading CDRs...")
    inv = load_all(BASE)
    analysis = analyze(inv)
    payload = build_payload(inv, analysis)

    json_path = OUT_DIR / "graph_data.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    html = HTML_TEMPLATE.replace("__GENERATED__", payload["generated"]).replace(
        "__DATA__", safe_json_for_script(payload)
    )
    html_path = OUT_DIR / "i2_relation_chart_interactive.html"
    html_path.write_text(html, encoding="utf-8")

    # Sanity checks
    assert (VENDOR / "vis-network.min.js").exists()
    assert "application/json" in html
    assert "vis.Network" in html
    print(f"nodes={len(payload['nodes'])} edges={len(payload['edges'])}")
    print(f"HTML: {html_path}")
    print(f"VIS : {VIS_JS} ({VIS_JS.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
