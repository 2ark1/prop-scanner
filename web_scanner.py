#!/usr/bin/env python3
"""
Prop Trade Strategy Scanner — Web Dashboard
────────────────────────────────────────────
Run locally:   python web_scanner.py
               Open http://localhost:5010

Deploy:        Push to GitHub → connect Render → done
"""

import sys, json, threading, time, warnings, urllib.request, urllib.parse
from datetime import datetime
warnings.filterwarnings('ignore')

missing = []
try:    import yfinance as yf
except: missing.append('yfinance')
try:    import pandas as pd; import numpy as np
except: missing.append('pandas numpy')
try:    from flask import Flask, jsonify, render_template_string
except: missing.append('flask')
try:    import pytz
except: missing.append('pytz')

if missing:
    print(f"Missing: pip install {' '.join(missing)}")
    sys.exit(1)

# ── Import scanner logic ───────────────────────────────────────────────────────
try:
    from scanner import INSTRUMENTS, AEST, scan_instrument
except ImportError as e:
    print(f"Cannot import scanner.py: {e}")
    sys.exit(1)

app = Flask(__name__)

# ── Telegram Alerts ────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = "8635424323:AAF2Knm3Upe6QVS1PggktGlfrIADmH5W600"
TELEGRAM_CHAT_ID = "-1003635453502"  # Prop scanner alerts channel

# Track which instruments have already been alerted so we don't spam
_alerted = set()

def send_telegram(message):
    """Send a message via Telegram bot."""
    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            'chat_id':    TELEGRAM_CHAT_ID,
            'text':       message,
            'parse_mode': 'HTML'
        }).encode()
        req  = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def build_alert(sym, r):
    """Build alert message for an A+ setup."""
    direction  = r.get('bos_direction', '—')
    dir_emoji  = '📈' if direction == 'Bullish' else '📉'
    price      = r.get('current_price')
    fibs       = r.get('fib_levels', {})
    fib618     = fibs.get('0.618')
    avwap      = r.get('avwap')
    poc        = r.get('poc')
    bias       = r.get('trend_bias', '—')
    bos_detail = r.get('bos_detail', '—')
    tfcot      = r.get('tfcot_detail', '—')
    near       = r.get('near_precision', [])

    def fp(v, s):
        if not v: return '—'
        if s in ('EURUSD',):           return f"{v:.4f}"
        if s in ('BTCUSD','US500','HK50'): return f"{v:,.2f}"
        return f"{v:.4f}"

    lines = [
        f"🟢 <b>A+ SETUP — {sym}</b> {dir_emoji}",
        f"Price: <b>{fp(price, sym)}</b>  |  Bias: {bias}",
        "",
        f"📍 BOS: {bos_detail}",
        f"📍 TFCOT: {tfcot}",
        "",
        "<b>Precision Levels:</b>",
        f"  0.618 Fib → {fp(fib618, sym)}",
    ]
    if avwap: lines.append(f"  AVWAP     → {fp(avwap, sym)}")
    if poc:   lines.append(f"  Vol POC   → {fp(poc, sym)}")
    if near:
        lines.append("")
        lines.append("<b>⚡ Near entry:</b>")
        for n in near:
            lines.append(f"  · {n}")
    lines.append("")
    lines.append(f"⏰ {datetime.now(AEST).strftime('%H:%M AEST, %d %b %Y')}")
    return "\n".join(lines)

def check_alerts(results):
    """Fire Telegram alert for any new A+ setups."""
    global _alerted
    current_aplus = {sym for sym, r in results.items() if r.get('score') == 3}
    new_alerts    = current_aplus - _alerted
    for sym in new_alerts:
        msg = build_alert(sym, results[sym])
        send_telegram(msg)
        print(f"[ALERT] Telegram sent for {sym}")
    # Clear alert state for instruments that drop out of A+
    _alerted = current_aplus

# ── Background auto-scan every 15 minutes ──────────────────────────────────────
_last_results = {}

def background_scan():
    global _last_results
    while True:
        try:
            results = {}
            for k, info in INSTRUMENTS.items():
                r = scan_instrument(k, info)
                results[k] = sanitize(r)
            _last_results = results
            check_alerts(results)
            now = datetime.now(AEST).strftime('%H:%M:%S')
            aplus = sum(1 for r in results.values() if r.get('score')==3)
            print(f"[{now}] Auto-scan complete — {aplus} A+ setup(s)")
        except Exception as e:
            print(f"[Auto-scan error] {e}")
        time.sleep(15 * 60)  # 15 minutes

# ── Sanitize NaN/Inf for JSON ─────────────────────────────────────────────────
def sanitize(obj):
    # Handle numpy floats, numpy ints, and plain Python floats
    if isinstance(obj, float) or (hasattr(np, 'floating') and isinstance(obj, np.floating)):
        try:
            f = float(obj)
            return None if (f != f or f == float('inf') or f == float('-inf')) else f
        except: return None
    if hasattr(np, 'integer') and isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, dict):  return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)): return [sanitize(i) for i in obj]
    if isinstance(obj, str):   return obj
    return obj

# ── Flask Routes ───────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/api/scan')
def api_scan():
    global _last_results
    now = datetime.now(AEST)
    results = {}
    for k, info in INSTRUMENTS.items():
        r = scan_instrument(k, info)
        results[k] = sanitize(r)
    _last_results = results
    check_alerts(results)  # also alert on manual scans
    return jsonify({
        'timestamp': now.strftime('%I:%M %p'),
        'date': now.strftime('%A, %d %b %Y'),
        'instruments': results,
    })

# ── HTML Dashboard ─────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Prop Trade Scanner</title>
<link href="https://api.fontshare.com/v2/css?f[]=cabinet-grotesk@700,800&f[]=satoshi@400,500,700&display=swap" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet"/>
<style>
:root {
  --bg:#080b0f; --surface:#0d1117; --surface2:#111620; --surface3:#161c27;
  --border:#1e2632; --text:#d4dbe8; --muted:#6b7a96; --faint:#2e3a52;
  --green:#00c26a; --green-glow:rgba(0,194,106,.15); --green-dim:#003d22;
  --gold:#e8af34; --gold-glow:rgba(232,175,52,.12); --gold-dim:rgba(232,175,52,.25);
  --blue:#3b82f6; --red:#f04358; --orange:#f59e0b;
  --font-d:'Cabinet Grotesk',sans-serif; --font-b:'Satoshi',sans-serif;
  --font-m:'JetBrains Mono',monospace;
  --r:.5rem; --r2:.75rem; --r3:1rem; --r-full:9999px;
  --ease:180ms cubic-bezier(.16,1,.3,1);
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{-webkit-font-smoothing:antialiased;scroll-behavior:smooth}
body{background:var(--bg);color:var(--text);font-family:var(--font-b);font-size:.9375rem;min-height:100vh;line-height:1.5}
button{cursor:pointer;background:none;border:none;font:inherit;color:inherit}
/* NAV */
.nav{position:sticky;top:0;z-index:100;background:rgba(8,11,15,.92);
  backdrop-filter:blur(16px);border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:1rem;padding:.75rem 1.5rem;flex-wrap:wrap}
.nav-logo{display:flex;align-items:center;gap:.6rem;font-family:var(--font-d);
  font-weight:800;font-size:1.05rem;letter-spacing:-.02em}
.nav-logo svg{flex-shrink:0}
.accent{color:var(--green)}
.clock{font-family:var(--font-m);font-size:.8rem;color:var(--muted);margin-left:auto}
.clock strong{color:var(--text)}
/* TOOLBAR */
.toolbar{padding:.75rem 1.5rem;display:flex;align-items:center;gap:.75rem;
  flex-wrap:wrap;border-bottom:1px solid var(--border);background:var(--surface)}
.filter-pill{font-family:var(--font-m);font-size:.72rem;font-weight:600;
  letter-spacing:.06em;padding:.3rem .9rem;border-radius:var(--r-full);
  border:1px solid var(--border);color:var(--muted);transition:all var(--ease);cursor:pointer}
.filter-pill.active-green{background:rgba(0,194,106,.12);border-color:rgba(0,194,106,.35);color:var(--green)}
.filter-pill.active-gold{background:rgba(232,175,52,.12);border-color:rgba(232,175,52,.35);color:var(--gold)}
.filter-pill.active-blue{background:rgba(59,130,246,.12);border-color:rgba(59,130,246,.35);color:var(--blue)}
.filter-pill.active-muted{background:rgba(107,122,150,.1);border-color:rgba(107,122,150,.25);color:var(--muted)}
.filter-pill.selected{box-shadow:0 0 0 2px currentColor}
.scan-btn{background:var(--green);color:#000;font-weight:700;font-size:.85rem;
  padding:.5rem 1.4rem;border-radius:var(--r-full);letter-spacing:.02em;margin-left:auto;
  transition:all var(--ease);display:flex;align-items:center;gap:.5rem}
.scan-btn:hover{background:#00e07a;transform:translateY(-1px)}
.scan-btn:disabled{opacity:.5;cursor:not-allowed;transform:none}
.scan-btn .spin{animation:spin 1s linear infinite;display:none}
.scan-btn.loading .spin{display:inline-block}
.scan-btn.loading .scan-icon{display:none}
@keyframes spin{to{transform:rotate(360deg)}}
/* MAIN */
.main{max-width:1280px;margin:0 auto;padding:1.25rem 1.5rem}
/* SUMMARY */
.summary{display:flex;gap:.75rem;flex-wrap:wrap;margin-bottom:1.5rem}
.summary-card{background:var(--surface);border:1px solid var(--border);
  border-radius:var(--r2);padding:.75rem 1.25rem;display:flex;
  flex-direction:column;gap:.2rem;min-width:120px}
.sc-label{font-family:var(--font-m);font-size:.68rem;letter-spacing:.08em;color:var(--muted)}
.sc-val{font-family:var(--font-m);font-weight:700;font-size:1.1rem}
.sc-val.green{color:var(--green)}.sc-val.gold{color:var(--gold)}.sc-val.blue{color:var(--blue)}.sc-val.red{color:var(--red)}
/* GRID */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:1rem}
/* CARD */
.card{background:var(--surface);border:1px solid var(--border);
  border-radius:var(--r3);overflow:hidden;
  transition:border-color var(--ease),box-shadow var(--ease)}
.card:hover{border-color:rgba(255,255,255,.12)}
.card.grade-aplus{border-color:var(--green);box-shadow:0 0 24px var(--green-glow)}
.card.grade-monitor{border-color:var(--gold)}
.card.hidden{display:none}
/* Card header */
.card-head{display:flex;align-items:flex-start;gap:.75rem;padding:1rem 1rem .75rem}
.type-icon{width:36px;height:36px;border-radius:var(--r);display:flex;
  align-items:center;justify-content:center;font-size:1rem;flex-shrink:0}
.icon-index{background:rgba(59,130,246,.15);color:var(--blue)}
.icon-metal{background:rgba(232,175,52,.15);color:var(--gold)}
.icon-forex{background:rgba(0,194,106,.12);color:var(--green)}
.icon-crypto{background:rgba(139,92,246,.15);color:#a78bfa}
.card-title{flex:1;min-width:0}
.card-name{font-family:var(--font-d);font-weight:800;font-size:1rem;letter-spacing:-.02em;line-height:1.2}
.card-sub{font-family:var(--font-m);font-size:.7rem;color:var(--muted);margin-top:.1rem}
.card-price-wrap{text-align:right}
.card-price{font-family:var(--font-m);font-weight:600;font-size:.95rem;white-space:nowrap}
.card-chg{font-family:var(--font-m);font-size:.72rem;margin-top:.1rem}
.chg-up{color:var(--green)}.chg-dn{color:var(--red)}.chg-flat{color:var(--muted)}
/* Rating badge */
.rating-badge{display:inline-flex;align-items:center;gap:.3rem;
  font-family:var(--font-m);font-size:.68rem;font-weight:600;letter-spacing:.04em;
  padding:.25rem .6rem;border-radius:var(--r-full);border:1px solid}
.badge-aplus{background:rgba(0,194,106,.15);border-color:rgba(0,194,106,.4);color:var(--green)}
.badge-monitor{background:rgba(232,175,52,.12);border-color:rgba(232,175,52,.35);color:var(--gold)}
.badge-watching{background:rgba(59,130,246,.1);border-color:rgba(59,130,246,.3);color:var(--blue)}
.badge-none{background:rgba(107,122,150,.1);border-color:rgba(107,122,150,.25);color:var(--muted)}
/* Score bar */
.score-wrap{padding:0 1rem .75rem}
.criteria-bar{display:flex;gap:3px;height:6px;border-radius:4px;overflow:hidden;margin-bottom:.4rem}
.crit-seg{flex:1;background:var(--faint);border-radius:2px;transition:background .4s}
.crit-seg.on-loc{background:var(--blue)}
.crit-seg.on-bos{background:var(--gold)}
.crit-seg.on-prec{background:var(--green)}
.crit-labels{display:flex;justify-content:space-between;font-family:var(--font-m);font-size:.62rem;color:var(--muted)}
/* Score meter */
.score-meter-wrap{display:flex;align-items:center;gap:.75rem;padding:0 1rem .75rem}
.score-bar-outer{flex:1;height:8px;background:var(--faint);border-radius:var(--r-full);overflow:hidden}
.score-bar-inner{height:100%;border-radius:var(--r-full);transition:width .6s cubic-bezier(.16,1,.3,1)}
.bar-0{background:var(--muted);width:0}
.bar-low{background:var(--blue)}
.bar-mid{background:var(--gold)}
.bar-high{background:var(--green);box-shadow:0 0 8px var(--green-glow)}
.score-pct{font-family:var(--font-m);font-weight:700;font-size:.8rem;min-width:2.5rem;text-align:right}
/* Direction + bias */
.meta-row{display:flex;align-items:center;gap:.5rem;padding:0 1rem .75rem;flex-wrap:wrap}
.dir-chip{font-family:var(--font-m);font-size:.7rem;font-weight:700;
  padding:.2rem .65rem;border-radius:var(--r-full)}
.dir-long{background:rgba(0,194,106,.12);color:var(--green);border:1px solid rgba(0,194,106,.3)}
.dir-short{background:rgba(240,67,88,.1);color:var(--red);border:1px solid rgba(240,67,88,.25)}
.dir-none{background:var(--surface2);color:var(--muted);border:1px solid var(--border)}
.bias-chip{font-family:var(--font-m);font-size:.7rem;color:var(--muted);
  padding:.2rem .65rem;border-radius:var(--r-full);border:1px solid var(--border);background:var(--surface2)}
/* Criteria list */
.criteria{padding:.75rem 1rem;border-top:1px solid var(--border)}
.cf-title{font-family:var(--font-m);font-size:.65rem;letter-spacing:.08em;color:var(--muted);margin-bottom:.5rem}
.cf-list{display:flex;flex-direction:column;gap:.35rem}
.cf-item{display:flex;align-items:flex-start;gap:.5rem;font-size:.73rem}
.cf-item.ok{color:var(--text)}.cf-item.no{color:var(--muted)}
.cf-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0;margin-top:.4rem}
.cf-ok{background:var(--green)}.cf-no{background:var(--faint)}
.cf-sub{font-family:var(--font-m);font-size:.65rem;color:var(--muted);margin-top:.1rem;padding-left:1rem}
/* Precision levels */
.prec-section{padding:.75rem 1rem;border-top:1px solid var(--border)}
.prec-row{display:flex;justify-content:space-between;align-items:center;
  padding:.3rem .5rem;border-radius:var(--r);margin-bottom:.25rem}
.prec-row-fib{background:rgba(59,130,246,.06);border:1px solid rgba(59,130,246,.15)}
.prec-row-avwap{background:rgba(232,175,52,.06);border:1px solid rgba(232,175,52,.15)}
.prec-row-poc{background:rgba(107,122,150,.06);border:1px solid rgba(107,122,150,.15)}
.prec-key{font-family:var(--font-m);font-size:.65rem;color:var(--muted);letter-spacing:.03em}
.prec-val{font-family:var(--font-m);font-weight:600;font-size:.8rem}
/* HTF levels */
.htf-section{padding:.75rem 1rem;border-top:1px solid var(--border)}
.htf-row{display:flex;justify-content:space-between;align-items:center;
  padding:.25rem .5rem;border-radius:var(--r);margin-bottom:.2rem;
  background:var(--surface2);border:1px solid var(--border)}
.htf-label{font-family:var(--font-m);font-size:.65rem;color:var(--muted);max-width:55%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.htf-right{display:flex;align-items:center;gap:.5rem}
.htf-val{font-family:var(--font-m);font-size:.75rem;font-weight:600}
.htf-dist{font-family:var(--font-m);font-size:.65rem}
.htf-up{color:var(--green)}.htf-dn{color:var(--red)}.htf-near{color:var(--gold)}
/* Options */
.opts-section{padding:.75rem 1rem;border-top:1px solid var(--border)}
.opts-row{display:flex;gap:.5rem;flex-wrap:wrap;margin-bottom:.5rem}
.opts-chip{font-family:var(--font-m);font-size:.68rem;padding:.2rem .55rem;
  border-radius:var(--r-full);border:1px solid var(--border);background:var(--surface2);color:var(--muted)}
.opts-bull{color:var(--green);border-color:rgba(0,194,106,.3);background:rgba(0,194,106,.06)}
.opts-bear{color:var(--red);border-color:rgba(240,67,88,.25);background:rgba(240,67,88,.05)}
/* Expand */
.expand-btn{width:100%;padding:.5rem;font-size:.72rem;color:var(--muted);
  border-top:1px solid var(--border);background:var(--surface2);
  transition:color var(--ease);letter-spacing:.03em;font-family:var(--font-m)}
.expand-btn:hover{color:var(--text)}
.expandable{display:none}
.expandable.open{display:block}
/* Skeleton */
.skeleton{animation:shimmer 1.5s infinite linear;
  background:linear-gradient(90deg,var(--surface2) 25%,var(--surface3) 50%,var(--surface2) 75%);
  background-size:200% 100%;border-radius:var(--r);height:1rem}
@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
/* Toast */
.toast{position:fixed;bottom:1.5rem;right:1.5rem;background:var(--surface);
  border:1px solid var(--border);border-radius:var(--r2);padding:.75rem 1.25rem;
  font-size:.8rem;box-shadow:0 8px 32px rgba(0,0,0,.5);z-index:200;
  opacity:0;transform:translateY(8px);transition:all .25s;pointer-events:none}
.toast.show{opacity:1;transform:none}
/* Responsive */
@media(max-width:600px){.main{padding:1rem}.nav{padding:.75rem 1rem}.grid{grid-template-columns:1fr}}
/* Scrollbar */
::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-track{background:var(--bg)}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
</style>
</head>
<body>

<!-- NAV -->
<nav class="nav">
  <div class="nav-logo">
    <svg width="26" height="26" viewBox="0 0 26 26" fill="none">
      <rect x="1" y="13" width="4" height="10" rx="1" fill="#00c26a"/>
      <rect x="7" y="7" width="4" height="16" rx="1" fill="#00c26a" opacity=".65"/>
      <rect x="13" y="3" width="4" height="20" rx="1" fill="#00c26a"/>
      <rect x="19" y="9" width="4" height="14" rx="1" fill="#e8af34"/>
    </svg>
    <span>Prop <span class="accent">Strategy</span> Scanner</span>
  </div>
  <div class="clock">
    <strong id="clock">--:--:-- --</strong> AEST
  </div>
</nav>

<!-- TOOLBAR -->
<div class="toolbar">
  <button class="filter-pill active-green" id="f-all"    onclick="setFilter('all')">ALL</button>
  <button class="filter-pill active-green" id="f-aplus"  onclick="setFilter('aplus')">🟢 A+ SETUP</button>
  <button class="filter-pill active-gold"  id="f-monitor"onclick="setFilter('monitor')">🟡 MONITOR</button>
  <button class="filter-pill active-blue"  id="f-watch"  onclick="setFilter('watch')">🟠 WATCHING</button>
  <button class="scan-btn" id="scan-btn" onclick="runScan()">
    <svg class="scan-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
    <svg class="spin" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10" opacity=".25"/><path d="M12 2a10 10 0 0 1 10 10"/></svg>
    SCAN ALL
  </button>
</div>

<!-- MAIN -->
<main class="main">
  <div class="summary" id="summary">
    <div class="summary-card"><span class="sc-label">A+ SETUPS</span><span class="sc-val green" id="s-aplus">—</span></div>
    <div class="summary-card"><span class="sc-label">MONITOR</span><span class="sc-val gold" id="s-monitor">—</span></div>
    <div class="summary-card"><span class="sc-label">WATCHING</span><span class="sc-val blue" id="s-watch">—</span></div>
    <div class="summary-card"><span class="sc-label">NO SETUP</span><span class="sc-val red" id="s-none">—</span></div>
    <div class="summary-card"><span class="sc-label">LAST SCAN</span><span class="sc-val" id="s-time" style="font-size:.8rem;color:var(--muted)">Not scanned</span></div>
  </div>
  <div class="grid" id="grid">
    <div style="grid-column:1/-1;text-align:center;padding:3rem;color:var(--muted);font-family:var(--font-m)">
      Press <strong style="color:var(--green)">SCAN ALL</strong> to start the scanner.
    </div>
  </div>
</main>

<div class="toast" id="toast"></div>

<script>
// ── Clock ─────────────────────────────────────────────────────────────────────
function updateClock() {
  const aest = new Date(new Date().toLocaleString('en-US',{timeZone:'Australia/Sydney'}));
  const h=aest.getHours(), m=aest.getMinutes(), s=aest.getSeconds();
  const ampm=h>=12?'PM':'AM';
  document.getElementById('clock').textContent =
    `${String(h%12||12).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')} ${ampm}`;
}
setInterval(updateClock,1000); updateClock();

// ── State ─────────────────────────────────────────────────────────────────────
let currentFilter = 'all';
let lastData = null;

// ── Filter ────────────────────────────────────────────────────────────────────
function setFilter(f) {
  currentFilter = f;
  ['all','aplus','monitor','watch'].forEach(id => {
    document.getElementById('f-'+id)?.classList.toggle('selected', id===f);
  });
  applyFilter();
}

function applyFilter() {
  document.querySelectorAll('.card').forEach(c => {
    if (currentFilter==='all') { c.classList.remove('hidden'); return; }
    c.classList.toggle('hidden', c.dataset.grade !== currentFilter);
  });
}

// ── Scan ──────────────────────────────────────────────────────────────────────
function runScan() {
  const btn = document.getElementById('scan-btn');
  btn.classList.add('loading'); btn.disabled = true;
  showToast('Scanning 6 instruments…');
  fetch('/api/scan')
    .then(r => r.json())
    .then(data => {
      lastData = data;
      renderSummary(data);
      renderGrid(data);
      document.getElementById('s-time').textContent = data.timestamp;
      btn.classList.remove('loading'); btn.disabled = false;
      showToast('Scan complete ✓', 2000);
    })
    .catch(e => {
      console.error(e);
      btn.classList.remove('loading'); btn.disabled = false;
      showToast('Scan failed — check console', 3000);
    });
}

// ── Summary ───────────────────────────────────────────────────────────────────
function renderSummary(data) {
  const inst = Object.values(data.instruments);
  document.getElementById('s-aplus').textContent   = inst.filter(i=>i.score===3).length;
  document.getElementById('s-monitor').textContent = inst.filter(i=>i.score===2).length;
  document.getElementById('s-watch').textContent   = inst.filter(i=>i.score===1).length;
  document.getElementById('s-none').textContent    = inst.filter(i=>i.score===0).length;
}

// ── Card rendering ────────────────────────────────────────────────────────────
const TYPE_ICONS = {
  EURUSD:{icon:'💱',cls:'icon-forex'},
  US500:{icon:'📊',cls:'icon-index'},
  XAUUSD:{icon:'🥇',cls:'icon-metal'},
  XAGUSD:{icon:'🪙',cls:'icon-metal'},
  BTCUSD:{icon:'₿',cls:'icon-crypto'},
  HK50:{icon:'📊',cls:'icon-index'},
};

function gradeKey(score) {
  if (score===3) return 'aplus';
  if (score===2) return 'monitor';
  if (score===1) return 'watch';
  return 'none';
}
function badgeClass(score) {
  return ['badge-none','badge-watching','badge-monitor','badge-aplus'][score] || 'badge-none';
}
function cardClass(score) {
  return ['','','grade-monitor','grade-aplus'][score] || '';
}
function barClass(score) {
  return ['bar-0','bar-low','bar-mid','bar-high'][score] || 'bar-0';
}
function barWidth(score) { return [0,33,66,100][score] || 0; }

function dirChipHtml(bos) {
  if (bos==='Bullish') return '<span class="dir-chip dir-long">↑ LONG</span>';
  if (bos==='Bearish') return '<span class="dir-chip dir-short">↓ SHORT</span>';
  return '<span class="dir-chip dir-none">— AWAITING BOS</span>';
}

function biasIcon(bias) {
  if (!bias) return '—';
  if (bias.includes('⬆⬆')) return '⬆⬆ Strong Bull';
  if (bias.includes('⬆'))  return '⬆ Bullish';
  if (bias.includes('⬇⬇')) return '⬇⬇ Strong Bear';
  if (bias.includes('⬇'))  return '⬇ Bearish';
  return bias;
}

function ratingLabel(score) {
  return ['⚫ No Setup','🟠 Watching','🟡 Monitor','🟢 A+ SETUP'][score] || '—';
}

function pct(p) {
  if (p===null||p===undefined||isNaN(p)) return '—';
  return (p>0?'+':'')+p.toFixed(2)+'%';
}

function fmtPrice(p, sym) {
  if (!p || isNaN(p)) return '—';
  if (['EURUSD'].includes(sym)) return p.toFixed(4);
  if (['BTCUSD','US500','HK50'].includes(sym)) return p.toLocaleString('en',{minimumFractionDigits:2,maximumFractionDigits:2});
  return p.toFixed(4);
}

function renderCard(sym, r) {
  if (r.error) {
    return `<div class="card" data-grade="none">
      <div class="card-head">
        <div class="type-icon icon-index">${TYPE_ICONS[sym]?.icon||'📊'}</div>
        <div class="card-title"><div class="card-name">${sym}</div></div>
      </div>
      <div style="padding:.5rem 1rem 1rem;font-size:.75rem;color:var(--red);font-family:var(--font-m)">⚠ ${r.error}</div>
    </div>`;
  }

  const score   = r.score || 0;
  const ti      = TYPE_ICONS[sym] || {icon:'📊',cls:'icon-index'};
  const price   = fmtPrice(r.current_price, sym);
  const chg     = r.daily_change_pct;
  const chgHtml = isNaN(chg) ? '' :
    `<div class="card-chg ${chg>0?'chg-up':chg<0?'chg-dn':'chg-flat'}">${pct(chg)}</div>`;
  const gk      = gradeKey(score);
  const bw      = barWidth(score);
  const locOk   = r.location_hit;
  const bosOk   = r.bos_direction && r.bos_direction !== 'None';
  const precOk  = r.precision_hit;

  // Criteria items
  const locReasons = (r.location_reasons||[]).slice(0,3).map(l=>`<div class="cf-sub">${l}</div>`).join('');
  const bosDetail  = r.bos_detail || '—';
  const tfcot      = r.tfcot_detail || '—';
  const precItems  = (r.near_precision||[]).map(p=>`<div class="cf-sub">${p}</div>`).join('');

  // Precision levels
  const fibs = r.fib_levels || {};
  const avwap = r.avwap;
  const poc   = r.poc;
  const precRows = [
    fibs['0.618'] ? `<div class="prec-row prec-row-fib"><span class="prec-key">0.618 FIB</span><span class="prec-val">${fmtPrice(fibs['0.618'],sym)}</span></div>` : '',
    avwap         ? `<div class="prec-row prec-row-avwap"><span class="prec-key">AVWAP</span><span class="prec-val">${fmtPrice(avwap,sym)}</span></div>` : '',
    poc           ? `<div class="prec-row prec-row-poc"><span class="prec-key">VOL POC</span><span class="prec-val">${fmtPrice(poc,sym)}</span></div>` : '',
  ].join('');

  // HTF levels — nearest 4
  const htfLevels = (r.htf_levels||[]).slice(0,4).map(l => {
    const isUp = l.price > r.current_price;
    const distCls = l.abs_distance_pct <= 0.6 ? 'htf-near' : isUp ? 'htf-up' : 'htf-dn';
    const dist = `${isUp?'▲':'▼'}${Math.abs(l.distance_pct).toFixed(2)}%`;
    const label = l.label.length > 30 ? l.label.slice(0,28)+'…' : l.label;
    return `<div class="htf-row">
      <span class="htf-label">${label}</span>
      <div class="htf-right">
        <span class="htf-val">${fmtPrice(l.price,sym)}</span>
        <span class="htf-dist ${distCls}">${dist}</span>
      </div>
    </div>`;
  }).join('');

  // Options
  let optsHtml = '';
  const g = r.gamma;
  if (g && !g.error && g.pcr !== null && g.pcr !== undefined) {
    const pcrLabel = g.pcr > 1 ? 'Bearish' : 'Bullish';
    const pcrCls   = g.pcr > 1 ? 'opts-bear' : 'opts-bull';
    optsHtml = `<div class="opts-section">
      <div class="cf-title">OPTIONS / GAMMA</div>
      <div class="opts-row">
        <span class="opts-chip ${pcrCls}">PCR ${g.pcr} ${pcrLabel}</span>
        ${g.max_pain    ? `<span class="opts-chip">Max Pain ${fmtPrice(g.max_pain,sym)}</span>` : ''}
        ${g.gamma_wall_up   ? `<span class="opts-chip opts-bear">γ Wall ▲ ${fmtPrice(g.gamma_wall_up,sym)}</span>` : ''}
        ${g.gamma_wall_down ? `<span class="opts-chip opts-bull">γ Wall ▼ ${fmtPrice(g.gamma_wall_down,sym)}</span>` : ''}
      </div>
    </div>`;
  }

  return `
<div class="card ${cardClass(score)}" data-grade="${gk}" id="card-${sym}">
  <!-- Header -->
  <div class="card-head">
    <div class="type-icon ${ti.cls}">${ti.icon}</div>
    <div class="card-title">
      <div class="card-name">${sym}</div>
      <div class="card-sub">${r.name||sym}</div>
    </div>
    <div class="card-price-wrap">
      <div class="card-price">${price}</div>
      ${chgHtml}
    </div>
  </div>
  <!-- Rating badge -->
  <div style="padding:0 1rem .75rem;display:flex;align-items:center;gap:.5rem;flex-wrap:wrap">
    <span class="rating-badge ${badgeClass(score)}">${ratingLabel(score)}</span>
    <span class="rating-badge" style="border-color:var(--border);color:var(--muted)">${score}/3 criteria</span>
  </div>
  <!-- Criteria progress bar -->
  <div class="score-wrap">
    <div class="criteria-bar">
      <div class="crit-seg ${locOk?'on-loc':''}"></div>
      <div class="crit-seg ${locOk?'on-loc':''}"></div>
      <div class="crit-seg ${bosOk?'on-bos':''}"></div>
      <div class="crit-seg ${bosOk?'on-bos':''}"></div>
      <div class="crit-seg ${precOk?'on-prec':''}"></div>
      <div class="crit-seg ${precOk?'on-prec':''}"></div>
    </div>
    <div class="crit-labels"><span>① Location</span><span>② BOS/TFCOT</span><span>③ Precision</span></div>
  </div>
  <!-- Score meter -->
  <div class="score-meter-wrap">
    <div class="score-bar-outer">
      <div class="score-bar-inner ${barClass(score)}" style="width:${bw}%"></div>
    </div>
    <span class="score-pct" style="color:${score===3?'var(--green)':score===2?'var(--gold)':score===1?'var(--blue)':'var(--muted)'}">${bw}%</span>
  </div>
  <!-- Direction + Bias -->
  <div class="meta-row">
    ${dirChipHtml(r.bos_direction)}
    <span class="bias-chip">${biasIcon(r.trend_bias)}</span>
  </div>
  <!-- Criteria detail -->
  <div class="criteria">
    <div class="cf-title">CHECKLIST</div>
    <div class="cf-list">
      <div class="cf-item ${locOk?'ok':'no'}">
        <span class="cf-dot ${locOk?'cf-ok':'cf-no'}"></span>
        <div><div>${locOk?'✓':'✗'} Location — HTF zone / EMA touch</div>${locOk?locReasons:''}</div>
      </div>
      <div class="cf-item ${bosOk?'ok':'no'}">
        <span class="cf-dot ${bosOk?'cf-ok':'cf-no'}"></span>
        <div>
          <div>${bosOk?'✓':'✗'} BOS (4H) + TFCOT (2H)</div>
          <div class="cf-sub">${bosDetail}</div>
          <div class="cf-sub">${tfcot}</div>
        </div>
      </div>
      <div class="cf-item ${precOk?'ok':'no'}">
        <span class="cf-dot ${precOk?'cf-ok':'cf-no'}"></span>
        <div><div>${precOk?'✓':'✗'} Precision — 0.618 / AVWAP / POC</div>${precOk?precItems:''}</div>
      </div>
    </div>
  </div>
  <!-- Precision levels -->
  ${precRows ? `<div class="prec-section"><div class="cf-title" style="margin-bottom:.5rem">PRECISION LEVELS</div>${precRows}</div>` : ''}
  <!-- Options -->
  ${optsHtml}
  <!-- Expand toggle -->
  <button class="expand-btn" onclick="toggleExpand('${sym}',this)">▼ HTF KEY LEVELS</button>
  <div class="expandable" id="expand-${sym}">
    ${htfLevels ? `<div class="htf-section">${htfLevels}</div>` : '<div style="padding:.75rem 1rem;font-size:.73rem;color:var(--muted);font-family:var(--font-m)">No HTF levels detected</div>'}
  </div>
</div>`;
}

function toggleExpand(sym, btn) {
  const el = document.getElementById('expand-'+sym);
  const open = el.classList.toggle('open');
  btn.textContent = (open ? '▲' : '▼') + ' HTF KEY LEVELS';
}

// ── Render grid ───────────────────────────────────────────────────────────────
function renderGrid(data) {
  const order = {3:0, 2:1, 1:2, 0:3};
  const sorted = Object.entries(data.instruments).sort(([,a],[,b]) =>
    (order[a.score]??3) - (order[b.score]??3)
  );
  document.getElementById('grid').innerHTML = sorted.map(([sym,r]) => renderCard(sym,r)).join('');
  applyFilter();
}

// ── Toast ─────────────────────────────────────────────────────────────────────
let toastTimer;
function showToast(msg, dur=4000) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(()=>t.classList.remove('show'), dur);
}

// ── Init ──────────────────────────────────────────────────────────────────────
setFilter('all');
setTimeout(()=>runScan(), 400);
</script>
</body>
</html>
"""

# ── Launch ─────────────────────────────────────────────────────────────────────
def open_browser():
    time.sleep(1.2)
    import webbrowser
    webbrowser.open('http://localhost:5010')

if __name__ == '__main__':
    print("\n" + "─"*60)
    print("  Prop Trade Strategy Scanner — Web Dashboard")
    print("─"*60)
    print("  Starting on http://localhost:5010")
    print("  Telegram alerts: ACTIVE (A+ setups only)")
    print("  Auto-scan: every 15 minutes")
    print("  Press Ctrl+C to stop")
    print("─"*60 + "\n")
    threading.Thread(target=open_browser, daemon=True).start()
    threading.Thread(target=background_scan, daemon=True).start()
    app.run(host='0.0.0.0', port=5010, debug=False, use_reloader=False)
