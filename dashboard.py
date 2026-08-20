"""Render the local SQLite store as a self-contained offline HTML file.

The output has no external references: data is embedded as JSON and charts are
generated as inline SVG. It requires no CDN, no build step and no network access
when viewed, so it opens correctly on an isolated workstation.

The light and dark palettes are stepped independently and checked for
colour-vision-deficiency separation and contrast against their respective chart
surfaces.
"""

from __future__ import annotations

import json
import sqlite3
from html import escape as html_escape
from typing import Any

_TEMPLATE = r"""<!doctype html>
<html lang="en" data-theme="__THEME__">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root {
    color-scheme: light;
    --page:            #f9f9f7;
    --surface-1:       #fcfcfb;
    --text-primary:    #0b0b0b;
    --text-secondary:  #52514e;
    --text-muted:      #898781;
    --grid:            #e1e0d9;
    --axis:            #c3c2b7;
    --border:          rgba(11,11,11,0.10);
    --series-1:        #2a78d6;
    --series-2:        #eb6834;
    --hover:           rgba(11,11,11,0.04);
  }
  @media (prefers-color-scheme: dark) {
    :root:where(:not([data-theme="light"])) {
      color-scheme: dark;
      --page:           #0d0d0d;
      --surface-1:      #1a1a19;
      --text-primary:   #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted:     #898781;
      --grid:           #2c2c2a;
      --axis:           #383835;
      --border:         rgba(255,255,255,0.10);
      --series-1:       #3987e5;
      --series-2:       #d95926;
      --hover:          rgba(255,255,255,0.06);
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --page:           #0d0d0d;
    --surface-1:      #1a1a19;
    --text-primary:   #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted:     #898781;
    --grid:           #2c2c2a;
    --axis:           #383835;
    --border:         rgba(255,255,255,0.10);
    --series-1:       #3987e5;
    --series-2:       #d95926;
    --hover:          rgba(255,255,255,0.06);
  }

  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--page); color: var(--text-primary);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    font-size: 14px; line-height: 1.5;
  }
  .wrap { max-width: 1280px; margin: 0 auto; padding: 32px 24px 72px; }

  header.top { display: flex; align-items: flex-start; gap: 16px; flex-wrap: wrap; margin-bottom: 4px; }
  header.top h1 { font-size: 20px; font-weight: 600; margin: 0; letter-spacing: -0.01em; }
  header.top .sub { color: var(--text-secondary); font-size: 13px; margin-top: 2px; }
  header.top .spacer { flex: 1 1 auto; }

  button.ghost {
    font: inherit; font-size: 13px; color: var(--text-secondary);
    background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 8px; padding: 6px 12px; cursor: pointer;
  }
  button.ghost:hover { background: var(--hover); color: var(--text-primary); }
  button.ghost[aria-pressed="true"] { color: var(--text-primary); font-weight: 600; }

  /* one filter row, above everything it scopes */
  .filters {
    display: flex; gap: 10px; flex-wrap: wrap; align-items: center;
    margin: 20px 0 24px; padding: 12px 14px;
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px;
  }
  .filters .field { display: inline-flex; align-items: center; gap: 6px; }
  .filters label { font-size: 12px; color: var(--text-muted); margin-right: 2px; }
  .filters select, .filters input {
    font: inherit; font-size: 13px; color: var(--text-primary);
    background: var(--page); border: 1px solid var(--border);
    border-radius: 8px; padding: 5px 9px; max-width: 280px;
  }
  .filters .count { margin-left: auto; font-size: 12px; color: var(--text-muted); font-variant-numeric: tabular-nums; }

  .hero { padding: 4px 0 24px; }
  .hero .label { font-size: 13px; color: var(--text-secondary); }
  .hero .value { font-size: 56px; font-weight: 600; letter-spacing: -0.025em; line-height: 1.05; }
  .hero .note  { font-size: 12px; color: var(--text-muted); margin-top: 2px; }

  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 28px; }
  .tile { background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
  .tile .label { font-size: 12px; color: var(--text-secondary); }
  .tile .value { font-size: 24px; font-weight: 600; letter-spacing: -0.01em; margin-top: 2px; }
  .tile .sub { font-size: 11px; color: var(--text-muted); margin-top: 2px; font-variant-numeric: tabular-nums; }

  .grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(430px, 1fr)); gap: 16px; }
  .card {
    background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 12px; padding: 16px 18px 18px; margin-bottom: 16px;
    min-width: 0;              /* let flex/grid children shrink instead of overflowing */
  }
  .card > header { display: flex; align-items: baseline; gap: 12px; margin-bottom: 2px; }
  .card h2 { font-size: 14px; font-weight: 600; margin: 0; }
  .card .desc { font-size: 12px; color: var(--text-muted); margin: 0 0 12px; }
  .card .toggle { margin-left: auto; }

  .legend { display: flex; gap: 16px; flex-wrap: wrap; margin: 0 0 10px; }
  .legend .item { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-secondary); }
  .legend .swatch { width: 10px; height: 10px; border-radius: 3px; flex: none; }

  .plot { width: 100%; }
  .plot svg { display: block; width: 100%; height: auto; overflow: visible; }

  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
  th { font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-muted); font-weight: 600; white-space: nowrap; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  tbody tr:hover { background: var(--hover); }
  .tablewrap { max-height: 420px; overflow: auto; }
  td .q { color: var(--text-secondary); display: block; max-width: 620px;
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .hidden { display: none !important; }
  .empty { color: var(--text-muted); font-size: 13px; padding: 28px 0; text-align: center; }

  #tooltip {
    position: fixed; pointer-events: none; z-index: 50; opacity: 0;
    transition: opacity .08s linear;
    background: var(--surface-1); color: var(--text-primary);
    border: 1px solid var(--border); border-radius: 8px;
    padding: 8px 10px; font-size: 12px; line-height: 1.45;
    box-shadow: 0 6px 24px rgba(0,0,0,.18); max-width: 340px;
  }
  #tooltip .tt-title { font-weight: 600; margin-bottom: 3px; word-break: break-word; }
  #tooltip .tt-row { display: flex; gap: 10px; justify-content: space-between; }
  #tooltip .tt-row span:last-child { font-variant-numeric: tabular-nums; }
  #tooltip .tt-key { display: inline-flex; align-items: center; gap: 5px; color: var(--text-secondary); }
  #tooltip .tt-dot { width: 8px; height: 8px; border-radius: 2px; flex: none; }

  footer.note { margin-top: 32px; font-size: 12px; color: var(--text-muted); }
  footer.note code { font-size: 11px; background: var(--surface-1); border: 1px solid var(--border);
                     border-radius: 4px; padding: 1px 5px; }
</style>
</head>
<body>
<div class="wrap">

  <header class="top">
    <div>
      <h1>__TITLE__</h1>
      <div class="sub">__SUBTITLE__</div>
    </div>
    <div class="spacer"></div>
    <button class="ghost" id="themeBtn" type="button">Toggle theme</button>
  </header>

  <div class="filters">
    <span class="field"><label for="fRange">Range</label>
      <select id="fRange">
        <option value="0">All collected</option>
        <option value="1">Last 24 hours</option>
        <option value="7">Last 7 days</option>
        <option value="30">Last 30 days</option>
        <option value="90">Last 90 days</option>
      </select></span>
    <span class="field"><label for="fSurface">Surface</label>
      <select id="fSurface"><option value="">All surfaces</option></select></span>
    <span class="field"><label for="fUser">User</label>
      <select id="fUser"><option value="">All users</option></select></span>
    <span class="field"><label for="fModel">Model</label>
      <select id="fModel"><option value="">All models</option></select></span>
    <span class="field"><label for="fAgent">Agent</label>
      <select id="fAgent"><option value="">All agents</option></select></span>
    <span class="count" id="fCount"></span>
  </div>

  <section class="hero">
    <div class="label">Total tokens</div>
    <div class="value" id="heroValue">0</div>
    <div class="note" id="heroNote"></div>
  </section>

  <section class="kpis" id="kpis"></section>

  <div class="card">
    <header>
      <h2>Token usage over time</h2>
      <button class="ghost toggle" type="button" data-view="daily">Table</button>
    </header>
    <p class="desc">Input and output tokens per day, stacked. Hover a column for the breakdown.</p>
    <div class="legend" id="legendDaily"></div>
    <div class="plot" id="plotDaily"></div>
    <div class="tablewrap hidden" id="tableDaily"></div>
  </div>

  <div class="grid2">
    <div class="card">
      <header>
        <h2>Tokens by user</h2>
        <button class="ghost toggle" type="button" data-view="user">Table</button>
      </header>
      <p class="desc">Who is consuming the tokens, input vs output.</p>
      <div class="legend" id="legendUser"></div>
      <div class="plot" id="plotUser"></div>
      <div class="tablewrap hidden" id="tableUser"></div>
    </div>

    <div class="card">
      <header>
        <h2>Tokens by model</h2>
        <button class="ghost toggle" type="button" data-view="model">Table</button>
      </header>
      <p class="desc">Total tokens attributed to each model.</p>
      <div class="plot" id="plotModel"></div>
      <div class="tablewrap hidden" id="tableModel"></div>
    </div>

    <div class="card">
      <header>
        <h2>Chat turns by agent</h2>
        <button class="ghost toggle" type="button" data-view="agent">Table</button>
      </header>
      <p class="desc">Distinct user turns handled by each agent. Deep Research and its research
    sub-agents count as one.</p>
      <div class="plot" id="plotAgent"></div>
      <div class="tablewrap hidden" id="tableAgent"></div>
    </div>

    <div class="card">
      <header>
        <h2>Busiest hours</h2>
        <button class="ghost toggle" type="button" data-view="hour">Table</button>
      </header>
      <p class="desc">Total tokens by hour of day (UTC).</p>
      <div class="plot" id="plotHour"></div>
      <div class="tablewrap hidden" id="tableHour"></div>
    </div>
  </div>

  <div class="card">
    <header><h2>Per-user detail</h2></header>
    <p class="desc">Every value in the charts above, as numbers.</p>
    <div class="tablewrap" id="tableUserDetail"></div>
  </div>

  <div class="card">
    <header><h2>Recent activity</h2></header>
    <p class="desc">Most recent chat turns, newest first.</p>
    <div class="tablewrap" id="tableRecent"></div>
  </div>

  <div class="card hidden" id="cardNblm">
    <header><h2>NotebookLM Enterprise activity</h2></header>
    <p class="desc">From the NotebookLM Enterprise activity log: action counts only, over the whole
    collected window. The log carries no token data, so nothing here contributes to the token
    figures above, and the filter row does not apply.</p>
    <div class="tablewrap" id="tableNblm"></div>
  </div>

  <footer class="note">
    <p>Sources: Cloud Logging <code>gemini_enterprise_user_activity</code> (user identity, prompt)
    joined to Cloud Trace <code>gen_ai.usage.*</code> span attributes (token counts) — on trace id,
    or on session id where a call ran under its own trace but its spans name the session.
    Times are UTC.</p>
  </footer>
</div>

<div id="tooltip" role="status" aria-live="polite"></div>

<script>
const DATA = __DATA__;
const META = __META__;

/* ---------------- utilities ---------------- */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
  (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const num = (n) => (n == null ? "—" : Number(n).toLocaleString("en-US"));

function compact(n) {
  if (n == null) return "—";
  const a = Math.abs(n);
  if (a >= 1e9) return (n / 1e9).toFixed(a >= 1e10 ? 0 : 1).replace(/\.0$/, "") + "B";
  if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e7 ? 0 : 1).replace(/\.0$/, "") + "M";
  if (a >= 1e3) return (n / 1e3).toFixed(a >= 1e4 ? 0 : 1).replace(/\.0$/, "") + "K";
  return String(n);
}
const shortUser = (u) => (u || "").split("@")[0] || "(unattributed)";
function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

/* Rounded on the data-end only, square at the baseline. */
function barPathH(x, y, w, h, r) {          // horizontal bar, grows right
  r = Math.max(0, Math.min(r, w, h / 2));
  if (w <= 0.01) return "";
  return `M${x},${y} H${x + w - r} A${r},${r} 0 0 1 ${x + w},${y + r}`
       + ` V${y + h - r} A${r},${r} 0 0 1 ${x + w - r},${y + h} H${x} Z`;
}
function barPathV(x, y, w, h, r) {          // column, grows up (y is the top)
  r = Math.max(0, Math.min(r, h, w / 2));
  if (h <= 0.01) return "";
  return `M${x},${y + h} V${y + r} A${r},${r} 0 0 1 ${x + r},${y}`
       + ` H${x + w - r} A${r},${r} 0 0 1 ${x + w},${y + r} V${y + h} Z`;
}
function niceTicks(max, count) {
  if (!max || max <= 0) return [0];
  const raw = max / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || 10 * mag;
  const out = [];
  for (let v = 0; ; v += step) {   // the last tick always covers max, so no
    out.push(v);                   // column can overdraw the plot area
    if (v >= max) break;
  }
  return out;
}

/* ---------------- tooltip ---------------- */
const tip = $("tooltip");
function showTip(evt, html) {
  tip.innerHTML = html;
  tip.style.opacity = "1";
  const pad = 14, r = tip.getBoundingClientRect();
  let x = evt.clientX + pad, y = evt.clientY + pad;
  if (x + r.width > window.innerWidth - 8) x = evt.clientX - r.width - pad;
  if (y + r.height > window.innerHeight - 8) y = evt.clientY - r.height - pad;
  tip.style.left = Math.max(8, x) + "px";
  tip.style.top = Math.max(8, y) + "px";
}
const hideTip = () => { tip.style.opacity = "0"; };
function ttRow(color, key, val) {
  const dot = color ? `<span class="tt-dot" style="background:${color}"></span>` : "";
  return `<div class="tt-row"><span class="tt-key">${dot}${esc(key)}</span><span>${esc(val)}</span></div>`;
}
/* Attach hover to a mark plus an invisible, generously sized hit target. */
function hit(el, html) {
  el.style.cursor = "default";
  el.addEventListener("mousemove", (e) => showTip(e, html));
  el.addEventListener("mouseleave", hideTip);
}

const SVGNS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs) {
  const el = document.createElementNS(SVGNS, tag);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  return el;
}

/* ---------------- filtering ---------------- */
function currentRows() {
  const days = Number($("fRange").value);
  const u = $("fUser").value, m = $("fModel").value,
        a = $("fAgent").value, f = $("fSurface").value;
  let cutoff = null;
  if (days > 0 && META.maxTime) {
    const end = new Date(META.maxTime + "Z");
    cutoff = new Date(end.getTime() - days * 864e5).toISOString().slice(0, 19);
  }
  return DATA.rows.filter((r) =>
    (!cutoff || r.t >= cutoff) && (!f || r.f === f) &&
    (!u || r.u === u) && (!m || r.m === m) && (!a || (r.a || "(none)") === a));
}

/* Past ~7 classes adjacent categories blur, so fold the tail rather than
   render 30 near-identical bars. The full list stays in the table twin. */
function capTail(data, limit) {
  if (data.length <= limit) return data;
  const head = data.slice(0, limit);
  const tail = data.slice(limit);
  const other = tail.reduce((acc, d) => {
    acc.input += d.input || 0; acc.output += d.output || 0;
    acc.total += d.total || 0; acc.calls += d.calls || 0; acc.turns += d.turns || 0;
    return acc;
  }, { key: `Other (${tail.length})`, input: 0, output: 0, total: 0, calls: 0, turns: 0 });
  return [...head, other];
}

function groupSum(rows, keyFn) {
  const map = new Map();
  for (const r of rows) {
    const k = keyFn(r) ?? "(none)";
    let e = map.get(k);
    if (!e) map.set(k, (e = { key: k, input: 0, output: 0, total: 0, calls: 0, traces: new Set() }));
    e.input += r.i; e.output += r.o; e.total += r.i + r.o; e.calls += 1; e.traces.add(r.r);
  }
  return [...map.values()].map((e) => ({ ...e, turns: e.traces.size }));
}

/* ---------------- charts ---------------- */

/* Stacked columns over time. Two series -> legend + categorical slots 1 and 2. */
function drawDaily(rows) {
  const host = $("plotDaily");
  host.innerHTML = "";
  const byDay = new Map();
  for (const r of rows) {
    const d = r.t.slice(0, 10);
    let e = byDay.get(d);
    if (!e) byDay.set(d, (e = { key: d, input: 0, output: 0 }));
    e.input += r.i; e.output += r.o;
  }
  const data = [...byDay.values()].sort((a, b) => a.key < b.key ? -1 : 1);
  if (!data.length) { host.innerHTML = '<div class="empty">No data in this range.</div>'; return; }

  const c1 = css("--series-1"), c2 = css("--series-2");
  const W = Math.max(host.clientWidth || 720, 360);
  const padL = 56, padR = 16, padT = 12, padB = 34, plotH = 240;
  const H = plotH + padT + padB;
  const innerW = W - padL - padR;
  const maxV = Math.max(...data.map((d) => d.input + d.output), 1);
  const ticks = niceTicks(maxV, 4);
  const top = ticks[ticks.length - 1] || maxV;
  const y = (v) => padT + plotH - (v / top) * plotH;
  const band = innerW / data.length;
  const bw = Math.min(24, Math.max(4, band - 10));

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });

  for (const t of ticks) {                                  // hairline solid grid
    svg.appendChild(svgEl("line", { x1: padL, x2: W - padR, y1: y(t), y2: y(t),
      stroke: t === 0 ? css("--axis") : css("--grid"), "stroke-width": 1 }));
    const lab = svgEl("text", { x: padL - 10, y: y(t) + 4, "text-anchor": "end",
      fill: css("--text-muted"), "font-size": 11, "font-variant-numeric": "tabular-nums" });
    lab.textContent = compact(t);
    svg.appendChild(lab);
  }

  data.forEach((d, i) => {
    const x = padL + i * band + (band - bw) / 2;
    const total = d.input + d.output;
    const hTotal = (total / top) * plotH;
    const hOut = (d.output / top) * plotH;
    const GAP = 2;                                          // surface gap, not a stroke
    const hIn = Math.max(0, hTotal - hOut - (d.output > 0 ? GAP : 0));
    const yTop = padT + plotH - hTotal;

    const html = `<div class="tt-title">${esc(d.key)}</div>`
      + ttRow(c2, "Output", num(d.output)) + ttRow(c1, "Input", num(d.input))
      + ttRow(null, "Total", num(total));

    if (d.output > 0) {                                     // outermost -> rounded cap
      const p = svgEl("path", { d: barPathV(x, yTop, bw, hOut, 4), fill: c2 });
      svg.appendChild(p); hit(p, html);
    }
    if (hIn > 0) {
      const p = svgEl("path", { d: barPathV(x, yTop + hOut + (d.output > 0 ? GAP : 0), bw, hIn,
        d.output > 0 ? 0 : 4), fill: c1 });
      svg.appendChild(p); hit(p, html);
    }
    const target = svgEl("rect", { x: padL + i * band, y: padT, width: band, height: plotH,
      fill: "transparent" });                               // generous hit area
    svg.appendChild(target); hit(target, html);

    const every = Math.ceil(data.length / Math.max(2, Math.floor(innerW / 74)));
    if (i % every === 0 || i === data.length - 1) {
      const lab = svgEl("text", { x: x + bw / 2, y: H - 12, "text-anchor": "middle",
        fill: css("--text-muted"), "font-size": 11 });
      lab.textContent = d.key.slice(5);
      svg.appendChild(lab);
    }
  });

  // Direct-label the final column only: selective, never a number on every mark.
  const last = data[data.length - 1];
  const lastTotal = last.input + last.output;
  if (lastTotal > 0) {
    const x = padL + (data.length - 1) * band + band / 2;
    const yv = y(lastTotal) - 8;
    const lab = svgEl("text", { x, y: Math.max(padT + 9, yv), "text-anchor": "middle",
      fill: css("--text-secondary"), "font-size": 11, "font-weight": 600 });
    lab.textContent = compact(lastTotal);
    svg.appendChild(lab);
  }
  host.appendChild(svg);
}

/* Horizontal stacked bars. Two series -> legend + slots 1 and 2. */
function drawUser(rows) {
  const host = $("plotUser");
  host.innerHTML = "";
  const data = groupSum(rows, (r) => r.u).sort((a, b) => b.total - a.total).slice(0, 12);
  if (!data.length) { host.innerHTML = '<div class="empty">No data in this range.</div>'; return; }

  const c1 = css("--series-1"), c2 = css("--series-2");
  const W = Math.max(host.clientWidth || 520, 340);
  const padL = 128, padR = 60, padT = 6, padB = 4;
  const rowH = 30, bh = Math.min(24, rowH - 8);
  const H = padT + data.length * rowH + padB;
  const innerW = W - padL - padR;
  const maxV = Math.max(...data.map((d) => d.total), 1);

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });

  data.forEach((d, i) => {
    const yTop = padT + i * rowH + (rowH - bh) / 2;
    const GAP = 2;
    const wTotal = (d.total / maxV) * innerW;
    const wIn = (d.input / maxV) * innerW;
    const wOut = Math.max(0, wTotal - wIn - (d.input > 0 && d.output > 0 ? GAP : 0));

    const html = `<div class="tt-title">${esc(d.key)}</div>`
      + ttRow(c1, "Input", num(d.input)) + ttRow(c2, "Output", num(d.output))
      + ttRow(null, "Total", num(d.total)) + ttRow(null, "Turns", num(d.turns));

    if (wIn > 0) {
      const p = svgEl("path", { d: barPathH(padL, yTop, wIn, bh, d.output > 0 ? 0 : 4), fill: c1 });
      svg.appendChild(p); hit(p, html);
    }
    if (wOut > 0) {
      const p = svgEl("path", { d: barPathH(padL + wIn + (d.input > 0 ? GAP : 0), yTop, wOut, bh, 4), fill: c2 });
      svg.appendChild(p); hit(p, html);
    }

    const nameText = shortUser(d.key);
    const name = svgEl("text", { x: padL - 10, y: yTop + bh / 2 + 4, "text-anchor": "end",
      fill: css("--text-secondary"), "font-size": 12 });
    name.textContent = nameText.length > 18 ? nameText.slice(0, 17) + "…" : nameText;
    svg.appendChild(name);

    const val = svgEl("text", { x: padL + wTotal + 8, y: yTop + bh / 2 + 4,
      fill: css("--text-secondary"), "font-size": 11, "font-variant-numeric": "tabular-nums" });
    val.textContent = compact(d.total);                     // label outside the bar end
    svg.appendChild(val);

    const target = svgEl("rect", { x: 0, y: padT + i * rowH, width: W, height: rowH, fill: "transparent" });
    svg.appendChild(target); hit(target, html);
  });
  host.appendChild(svg);
}

/* Single-series horizontal bars: one measure over nominal categories -> one hue, no legend. */
function drawSingle(hostId, data, opts) {
  const host = $(hostId);
  host.innerHTML = "";
  if (!data.length) { host.innerHTML = '<div class="empty">No data in this range.</div>'; return; }

  const c1 = css("--series-1");
  const W = Math.max(host.clientWidth || 520, 340);
  const padL = opts.padL || 128, padR = 60, padT = 6, padB = 4;
  const rowH = 30, bh = Math.min(24, rowH - 8);
  const H = padT + data.length * rowH + padB;
  const innerW = W - padL - padR;
  const maxV = Math.max(...data.map((d) => d.value), 1);

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
  data.forEach((d, i) => {
    const yTop = padT + i * rowH + (rowH - bh) / 2;
    const w = (d.value / maxV) * innerW;
    const html = `<div class="tt-title">${esc(d.label)}</div>`
      + (d.tip || ttRow(null, opts.unit, num(d.value)));

    if (w > 0) {
      const p = svgEl("path", { d: barPathH(padL, yTop, w, bh, 4), fill: c1 });
      svg.appendChild(p); hit(p, html);
    }
    const name = svgEl("text", { x: padL - 10, y: yTop + bh / 2 + 4, "text-anchor": "end",
      fill: css("--text-secondary"), "font-size": 12 });
    name.textContent = d.label.length > 20 ? d.label.slice(0, 19) + "…" : d.label;
    svg.appendChild(name);

    const val = svgEl("text", { x: padL + w + 8, y: yTop + bh / 2 + 4,
      fill: css("--text-secondary"), "font-size": 11, "font-variant-numeric": "tabular-nums" });
    val.textContent = compact(d.value);
    svg.appendChild(val);

    const target = svgEl("rect", { x: 0, y: padT + i * rowH, width: W, height: rowH, fill: "transparent" });
    svg.appendChild(target); hit(target, html);
  });
  host.appendChild(svg);
}

/* ---------------- tables (the WCAG-clean twin of every chart) ---------------- */
function renderTable(hostId, cols, rows) {
  const head = cols.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("");
  const body = rows.map((r) =>
    "<tr>" + cols.map((c) => `<td class="${c.num ? "num" : ""}">${c.html ? c.get(r) : esc(c.get(r))}</td>`).join("") + "</tr>"
  ).join("");
  $(hostId).innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function legend(hostId, items) {
  $(hostId).innerHTML = items.map((it) =>
    `<span class="item"><span class="swatch" style="background:${it.color}"></span>${esc(it.label)}</span>`
  ).join("");
}

/* ---------------- render ---------------- */
function render() {
  const rows = currentRows();
  const c1 = css("--series-1"), c2 = css("--series-2");

  const totalIn = rows.reduce((s, r) => s + r.i, 0);
  const totalOut = rows.reduce((s, r) => s + r.o, 0);
  const traces = new Set(rows.map((r) => r.r));
  const sessions = new Set(rows.map((r) => r.s).filter(Boolean));

  $("heroValue").textContent = num(totalIn + totalOut);
  $("heroNote").textContent = rows.length
    ? `${num(rows.length)} model calls · ${rows[rows.length - 1].t.replace("T", " ")} → ${rows[0].t.replace("T", " ")} UTC`
    : "No model calls in this range.";
  $("fCount").textContent = `${num(rows.length)} model calls · ${num(traces.size)} turns`;

  const attributed = rows.filter((r) => r.at).length;
  const viaSession = rows.filter((r) => r.v === "s").length;
  const namedUsers = new Set(rows.filter((r) => r.at).map((r) => r.u));
  const tiles = [
    ["Named users", num(namedUsers.size)],
    ["Chat turns", num(traces.size)],
    ["Sessions", num(sessions.size)],
    ["Model calls", num(rows.length)],
    ["Input tokens", num(totalIn)],
    ["Output tokens", num(totalOut)],
    ["Avg tokens / turn", traces.size ? num(Math.round((totalIn + totalOut) / traces.size)) : "—"],
    ["User-attributed calls",
     rows.length ? `${num(attributed)} (${Math.round(attributed / rows.length * 100)}%)` : "—",
     viaSession ? `${num(attributed - viaSession)} via trace · ${num(viaSession)} via session` : ""],
  ];
  $("kpis").innerHTML = tiles.map(([l, v, sub]) =>
    `<div class="tile"><div class="label">${esc(l)}</div><div class="value">${esc(v)}</div>`
    + (sub ? `<div class="sub">${esc(sub)}</div>` : "") + `</div>`).join("");

  legend("legendDaily", [{ color: c1, label: "Input tokens" }, { color: c2, label: "Output tokens" }]);
  legend("legendUser", [{ color: c1, label: "Input tokens" }, { color: c2, label: "Output tokens" }]);

  drawDaily(rows);
  drawUser(rows);

  const byModel = groupSum(rows, (r) => r.m).sort((a, b) => b.total - a.total);
  drawSingle("plotModel", capTail(byModel, 10).map((d) => ({
    label: d.key, value: d.total,
    tip: ttRow(null, "Input", num(d.input)) + ttRow(null, "Output", num(d.output))
       + ttRow(null, "Total tokens", num(d.total)) + ttRow(null, "Calls", num(d.calls)),
  })), { unit: "Tokens", padL: 150 });

  const byAgent = groupSum(rows, (r) => r.a || "(none)").sort((a, b) => b.turns - a.turns);
  drawSingle("plotAgent", capTail(byAgent, 12).map((d) => ({
    label: d.key, value: d.turns,
    tip: ttRow(null, "Turns", num(d.turns)) + ttRow(null, "Model calls", num(d.calls))
       + ttRow(null, "Total tokens", num(d.total)),
  })), { unit: "Turns", padL: 150 });

  const byHour = groupSum(rows, (r) => r.t.slice(11, 13) + ":00").sort((a, b) => a.key < b.key ? -1 : 1);
  drawSingle("plotHour", byHour.map((d) => ({
    label: d.key, value: d.total,
    tip: ttRow(null, "Total tokens", num(d.total)) + ttRow(null, "Model calls", num(d.calls)),
  })), { unit: "Tokens", padL: 62 });

  // table twins
  const byDayMap = new Map();
  for (const r of rows) {
    const d = r.t.slice(0, 10);
    let e = byDayMap.get(d);
    if (!e) byDayMap.set(d, (e = { key: d, input: 0, output: 0, calls: 0 }));
    e.input += r.i; e.output += r.o; e.calls += 1;
  }
  const byDay = [...byDayMap.values()].sort((a, b) => a.key < b.key ? -1 : 1);

  renderTable("tableDaily", [
    { label: "Day", get: (r) => r.key },
    { label: "Input", num: true, get: (r) => num(r.input) },
    { label: "Output", num: true, get: (r) => num(r.output) },
    { label: "Total", num: true, get: (r) => num(r.input + r.output) },
    { label: "Calls", num: true, get: (r) => num(r.calls) },
  ], byDay);

  const byUser = groupSum(rows, (r) => r.u).sort((a, b) => b.total - a.total);
  const userCols = [
    { label: "User", get: (r) => r.key },
    { label: "Turns", num: true, get: (r) => num(r.turns) },
    { label: "Calls", num: true, get: (r) => num(r.calls) },
    { label: "Input", num: true, get: (r) => num(r.input) },
    { label: "Output", num: true, get: (r) => num(r.output) },
    { label: "Total", num: true, get: (r) => num(r.total) },
    { label: "Avg / turn", num: true, get: (r) => num(Math.round(r.total / Math.max(1, r.turns))) },
  ];
  renderTable("tableUser", userCols, byUser);
  renderTable("tableUserDetail", userCols, byUser);

  renderTable("tableModel", [
    { label: "Model", get: (r) => r.key },
    { label: "Calls", num: true, get: (r) => num(r.calls) },
    { label: "Input", num: true, get: (r) => num(r.input) },
    { label: "Output", num: true, get: (r) => num(r.output) },
    { label: "Total", num: true, get: (r) => num(r.total) },
  ], byModel);

  renderTable("tableAgent", [
    { label: "Agent", get: (r) => r.key },
    { label: "Turns", num: true, get: (r) => num(r.turns) },
    { label: "Calls", num: true, get: (r) => num(r.calls) },
    { label: "Total tokens", num: true, get: (r) => num(r.total) },
  ], byAgent);

  renderTable("tableHour", [
    { label: "Hour (UTC)", get: (r) => r.key },
    { label: "Calls", num: true, get: (r) => num(r.calls) },
    { label: "Total tokens", num: true, get: (r) => num(r.total) },
  ], byHour);

  // recent turns: aggregate this filtered slice back up to the turn
  const perTrace = new Map();
  for (const r of rows) {
    let e = perTrace.get(r.r);
    if (!e) perTrace.set(r.r, (e = { trace: r.r, t: r.t, u: r.u, a: r.a, models: new Set(), tokens: 0 }));
    e.models.add(r.m);
    e.tokens += r.i + r.o;
    if (r.t < e.t) e.t = r.t;
  }
  const recent = [...perTrace.values()].sort((a, b) => a.t < b.t ? 1 : -1).slice(0, 200);
  renderTable("tableRecent", [
    { label: "Time (UTC)", get: (r) => r.t.replace("T", " ") },
    { label: "User", get: (r) => r.u },
    { label: "Agent", get: (r) => r.a || "—" },
    { label: "Model", get: (r) => [...r.models].join(", ") },
    { label: "Tokens", num: true, get: (r) => num(r.tokens) },
    { label: "Prompt", html: true,
      get: (r) => `<span class="q" title="${esc(DATA.queries[r.trace] || "")}">${esc(DATA.queries[r.trace] || "—")}</span>` },
  ], recent);
}

/* ---------------- wiring ---------------- */
function fillSelect(id, values) {
  const sel = $(id), keep = sel.value;
  const first = sel.options[0].outerHTML;
  sel.innerHTML = first + values.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
  if (values.includes(keep)) sel.value = keep;
}
fillSelect("fSurface", [...new Set(DATA.rows.map((r) => r.f))].filter(Boolean).sort());
fillSelect("fUser", [...new Set(DATA.rows.map((r) => r.u))].sort());
fillSelect("fModel", [...new Set(DATA.rows.map((r) => r.m))].filter(Boolean).sort());
fillSelect("fAgent", [...new Set(DATA.rows.map((r) => r.a || "(none)"))].sort());

["fRange", "fSurface", "fUser", "fModel", "fAgent"]
  .forEach((id) => $(id).addEventListener("change", render));

/* NotebookLM activity is a separate fact with no tokens and no trace ids, so it
   is rendered once from its own rollup and left out of the filter row's scope. */
if (DATA.notebooklm.length) {
  $("cardNblm").classList.remove("hidden");
  renderTable("tableNblm", [
    { label: "User", get: (r) => r.u },
    { label: "Actions", num: true, get: (r) => num(r.n) },
    { label: "Notebooks", num: true, get: (r) => num(r.b) },
    { label: "Active days", num: true, get: (r) => num(r.d) },
    { label: "Last seen (UTC)", get: (r) => (r.l || "").replace("T", " ") },
  ], DATA.notebooklm);
}

document.querySelectorAll(".toggle").forEach((btn) => {
  btn.addEventListener("click", () => {
    const v = btn.dataset.view;
    const cap = v.charAt(0).toUpperCase() + v.slice(1);
    const plot = $("plot" + cap), table = $("table" + cap);
    const lg = $("legend" + cap);
    const showTable = plot.classList.toggle("hidden");
    table.classList.toggle("hidden", !showTable);
    if (lg) lg.classList.toggle("hidden", showTable);
    btn.textContent = showTable ? "Chart" : "Table";
    btn.setAttribute("aria-pressed", String(showTable));
  });
});

$("themeBtn").addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme");
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  render();                       // re-read CSS custom properties for the new mode
});

let raf = null;
new ResizeObserver(() => {
  if (raf) cancelAnimationFrame(raf);
  raf = requestAnimationFrame(render);
}).observe(document.querySelector(".wrap"));

$("fRange").value = "0";
render();
</script>
</body>
</html>
"""


def _embed_json(obj: Any) -> str:
    """Serialise `obj` for inclusion inside an inline <script> element.

    Prompt text is arbitrary user input. An HTML parser ends a script element at
    the first literal `</script>`, without regard for JavaScript string quoting,
    so a user who typed that into the assistant would truncate the page. `<`, `>`
    and `&` are emitted as \\uXXXX escapes, which JSON parses back to the original
    characters while leaving nothing for the HTML tokeniser to act on.
    """
    return (
        json.dumps(obj, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _short_model(model: str | None) -> str:
    """`projects/p/locations/l/publishers/google/models/gemini-x` -> `gemini-x`."""
    if not model:
        return "(unknown)"
    return model.rstrip("/").rsplit("/", 1)[-1] or model


def _rows_payload(conn: sqlite3.Connection, redact: bool) -> dict[str, Any]:
    """Compact JSON payload. Short keys keep the embedded blob small."""
    rows = [
        {
            "t": r["start_time"],
            "u": r["user_principal"],
            "m": _short_model(r["model"]),
            "a": r["agent_group"],
            "i": r["input_tokens"] or 0,
            "o": r["output_tokens"] or 0,
            "r": r["trace_id"],
            "s": r["session_id"],
            "f": r["surface"],
            "at": r["attributed"],
            "v": {"trace": "t", "session": "s"}.get(r["attributed_via"]),
        }
        for r in conn.execute(
            "SELECT * FROM usage WHERE start_time IS NOT NULL ORDER BY start_time DESC"
        )
    ]

    queries: dict[str, str] = {}
    if not redact:
        for r in conn.execute(
            "SELECT trace_id, query_text FROM turns WHERE query_text IS NOT NULL"
        ):
            text = r["query_text"].strip().replace("\n", " ")
            queries[r["trace_id"]] = text[:300]

    # Per-user rollup only: activity counts belong on the dashboard, prompt text
    # does not need to travel with them.
    notebooklm = [
        {
            "u": r["user_principal"],
            "n": r["activities"],
            "b": r["notebooks"],
            "d": r["active_days"],
            "l": r["last_seen"],
        }
        for r in conn.execute("SELECT * FROM notebooklm_by_user ORDER BY activities DESC")
    ]

    return {"rows": rows, "queries": queries, "notebooklm": notebooklm}


def render_dashboard(
    conn: sqlite3.Connection,
    *,
    project: str,
    title: str = "Gemini Enterprise usage",
    theme: str = "light",
    redact_queries: bool = False,
    generated_at: str = "",
) -> str:
    payload = _rows_payload(conn, redact_queries)
    rows = payload["rows"]

    max_time = max((r["t"] for r in rows), default="")
    min_time = min((r["t"] for r in rows), default="")
    turn_count = conn.execute("SELECT COUNT(*) AS c FROM turns").fetchone()["c"]
    nblm_count = conn.execute("SELECT COUNT(*) AS c FROM notebooklm_activity").fetchone()["c"]

    span = f"{min_time.replace('T', ' ')} → {max_time.replace('T', ' ')} UTC" if rows else "no data yet"
    subtitle = (
        f"Project {project} · {len(rows):,} model calls · {turn_count:,} logged turns"
        f"{f' · {nblm_count:,} NotebookLM activities' if nblm_count else ''}"
        f" · {span}"
        f"{' · generated ' + generated_at if generated_at else ''}"
        " · Cloud Logging + Cloud Trace"
    )

    meta = {"maxTime": max_time, "minTime": min_time, "project": project}

    return (
        _TEMPLATE.replace("__DATA__", _embed_json(payload))
        .replace("__META__", _embed_json(meta))
        .replace("__TITLE__", html_escape(title))
        .replace("__SUBTITLE__", html_escape(subtitle))
        .replace("__THEME__", theme)
    )
