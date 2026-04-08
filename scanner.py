#!/usr/bin/env python3
"""
Prop Trade Strategy Scanner
Instruments: XAUUSD, XAGUSD, BTCUSD, US500, GER40, HK50, USOIL, DXY
Rules: Location (HTF zone / 20-50 EMA) → BOS (2-4H) → Precision (0.618 / POC / AVWAP)
Runs daily after US market close. Sends structured report.
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
import json
import os

# ── Instrument Map ──────────────────────────────────────────────────────────────
INSTRUMENTS = {
    "EURUSD": {"ticker": "EURUSD=X", "name": "Euro/USD",      "pip": 0.0001},
    "US500":  {"ticker": "^GSPC",    "name": "S&P 500",       "pip": 0.25},
    "XAUUSD": {"ticker": "GC=F",     "name": "Gold",          "pip": 0.01},
    "XAGUSD": {"ticker": "SI=F",     "name": "Silver",        "pip": 0.001},
    "BTCUSD": {"ticker": "BTC-USD",  "name": "Bitcoin",       "pip": 1.0},
    "HK50":   {"ticker": "^HSI",     "name": "Hang Seng 50",  "pip": 1.0},
}

AEST = pytz.timezone("Australia/Sydney")


# ── Data Fetching ───────────────────────────────────────────────────────────────
def fetch_data(ticker, period="1y", interval="1d"):
    try:
        df = yf.download(ticker, period=period, interval=interval,
                         auto_adjust=True, progress=False)
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = pd.to_datetime(df.index)
        return df
    except Exception:
        return None


def fetch_weekly(ticker):
    try:
        df = yf.download(ticker, period="2y", interval="1wk",
                         auto_adjust=True, progress=False)
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = pd.to_datetime(df.index)
        return df
    except Exception:
        return None


def fetch_monthly(ticker):
    try:
        df = yf.download(ticker, period="5y", interval="1mo",
                         auto_adjust=True, progress=False)
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = pd.to_datetime(df.index)
        return df
    except Exception:
        return None


def fetch_4h(ticker):
    try:
        df = yf.download(ticker, period="30d", interval="4h",
                         auto_adjust=True, progress=False)
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = pd.to_datetime(df.index)
        return df
    except Exception:
        return None


def fetch_2h(ticker):
    """
    Yahoo Finance doesn't support 2H interval.
    Fetch 1H and resample to 2H candles.
    """
    try:
        df = yf.download(ticker, period="60d", interval="1h",
                         auto_adjust=True, progress=False)
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = pd.to_datetime(df.index)
        # Resample 1H → 2H
        df_2h = df.resample("2h").agg({
            "Open":   "first",
            "High":   "max",
            "Low":    "min",
            "Close":  "last",
            "Volume": "sum",
        }).dropna()
        return df_2h
    except Exception:
        return None


# ── Technical Helpers ───────────────────────────────────────────────────────────
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def find_pivot_highs_lows(df, left=3, right=3):
    """Detect pivot highs and lows using left/right bar confirmation."""
    highs, lows = [], []
    src_h = df['High'].values
    src_l = df['Low'].values
    for i in range(left, len(df) - right):
        if all(src_h[i] >= src_h[i-j] for j in range(1, left+1)) and \
           all(src_h[i] >= src_h[i+j] for j in range(1, right+1)):
            highs.append((df.index[i], float(src_h[i])))
        if all(src_l[i] <= src_l[i-j] for j in range(1, left+1)) and \
           all(src_l[i] <= src_l[i+j] for j in range(1, right+1)):
            lows.append((df.index[i], float(src_l[i])))
    return highs, lows


def cluster_levels(prices, threshold_pct=0.4):
    """
    Cluster nearby price levels to avoid duplicates.
    Returns deduplicated list sorted ascending.
    """
    if not prices:
        return []
    sorted_p = sorted(prices)
    clusters = [[sorted_p[0]]]
    for p in sorted_p[1:]:
        if abs(p - clusters[-1][-1]) / clusters[-1][-1] * 100 <= threshold_pct:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return [round(np.mean(c), 4) for c in clusters]


# ── HTF Level Detection ─────────────────────────────────────────────────────────
def detect_htf_levels(df_daily, df_weekly, df_monthly, current_price):
    """
    Comprehensive HTF level detection from multiple timeframes.
    Returns list of dicts: {price, label, timeframe, distance_pct}
    """
    raw_levels = []   # (price, label, timeframe)

    # ── 1. Monthly Swing Highs/Lows (major S/R — highest weight)
    if df_monthly is not None and len(df_monthly) >= 6:
        m_highs, m_lows = find_pivot_highs_lows(df_monthly, left=2, right=2)
        for _, p in m_highs[-4:]:
            raw_levels.append((p, "Monthly Resistance", "Monthly"))
        for _, p in m_lows[-4:]:
            raw_levels.append((p, "Monthly Support", "Monthly"))

    # ── 2. Weekly Swing Highs/Lows
    if df_weekly is not None and len(df_weekly) >= 10:
        w_highs, w_lows = find_pivot_highs_lows(df_weekly, left=3, right=3)
        for _, p in w_highs[-6:]:
            raw_levels.append((p, "Weekly Resistance", "Weekly"))
        for _, p in w_lows[-6:]:
            raw_levels.append((p, "Weekly Support", "Weekly"))

        # Previous week high/low (highly respected by institutions)
        if len(df_weekly) >= 2:
            prev_wk = df_weekly.iloc[-2]
            raw_levels.append((float(prev_wk['High']), "Prev Week High", "Weekly"))
            raw_levels.append((float(prev_wk['Low']),  "Prev Week Low",  "Weekly"))

    # ── 3. Daily Swing Highs/Lows (recent structure)
    if df_daily is not None and len(df_daily) >= 15:
        d_highs, d_lows = find_pivot_highs_lows(df_daily, left=4, right=4)
        for _, p in d_highs[-5:]:
            raw_levels.append((p, "Daily Resistance", "Daily"))
        for _, p in d_lows[-5:]:
            raw_levels.append((p, "Daily Support", "Daily"))

        # Previous day high/low
        if len(df_daily) >= 2:
            prev_day = df_daily.iloc[-2]
            raw_levels.append((float(prev_day['High']), "Prev Day High", "Daily"))
            raw_levels.append((float(prev_day['Low']),  "Prev Day Low",  "Daily"))

    # ── 4. High-Volume Consolidation Zones (daily)
    #       Identify periods where daily range was narrow (price accepted = support/demand zone)
    if df_daily is not None and len(df_daily) >= 20:
        recent = df_daily.tail(60).copy()
        recent['range_pct'] = (recent['High'] - recent['Low']) / recent['Low'] * 100
        low_range_days = recent[recent['range_pct'] < recent['range_pct'].quantile(0.25)]
        for _, row in low_range_days.iterrows():
            mid = float((row['High'] + row['Low']) / 2)
            raw_levels.append((mid, "Consolidation Zone", "Daily"))

    # ── 5. Round Number / Psychological Levels
    #       Auto-detect the appropriate round number interval for this instrument
    if current_price > 50000:    # BTC
        interval = 5000
    elif current_price > 3000:   # Gold, indices 5k+
        interval = 500
    elif current_price > 1000:
        interval = 100
    elif current_price > 100:
        interval = 50
    elif current_price > 10:
        interval = 5
    else:
        interval = 1

    if current_price and not (current_price != current_price):  # guard NaN
        base = round(current_price / interval) * interval
        for mult in [-3, -2, -1, 0, 1, 2, 3]:
            raw_levels.append((base + mult * interval, "Psychological Level", "HTF"))

    # ── Deduplicate and enrich ──────────────────────────────────────────────────
    # Group by price (cluster within 0.3%), keep most important label
    priority = {"Monthly": 0, "Weekly": 1, "Daily": 2, "HTF": 3}

    # Sort by price
    raw_levels.sort(key=lambda x: x[0])

    # Cluster nearby levels
    clustered = []
    used = [False] * len(raw_levels)
    for i, (p, lbl, tf) in enumerate(raw_levels):
        if used[i]:
            continue
        group = [(p, lbl, tf)]
        for j in range(i+1, len(raw_levels)):
            if used[j]:
                continue
            if abs(raw_levels[j][0] - p) / p * 100 <= 0.35:
                group.append(raw_levels[j])
                used[j] = True
        used[i] = True
        # Pick representative: highest-priority label
        group.sort(key=lambda x: priority.get(x[2], 99))
        best_p   = round(np.mean([g[0] for g in group]), 4)
        best_lbl = group[0][1]
        best_tf  = group[0][2]
        # Note if multiple timeframes confluent
        tfs = list(dict.fromkeys([g[2] for g in group]))
        if len(tfs) > 1:
            best_lbl = best_lbl + " ★CONFLUENCE"
            best_tf  = "/".join(tfs[:2])
        clustered.append({"price": best_p, "label": best_lbl, "timeframe": best_tf})

    # Calculate distance from current price and sort by proximity
    for lvl in clustered:
        lvl["distance_pct"] = round((lvl["price"] - current_price) / current_price * 100, 2)
        lvl["abs_distance_pct"] = abs(lvl["distance_pct"])

    clustered.sort(key=lambda x: x["abs_distance_pct"])
    return clustered


def check_location(current_price, htf_levels, ema20, ema50, bos_direction="None", threshold_pct=0.6):
    """
    Check if price is at an HTF level or EMA that aligns with BOS direction.
    - Bullish BOS → valid if price near support/demand level (price at or above level)
    - Bearish BOS → valid if price near resistance/supply level (price at or below level)
    - No BOS      → accept any nearby level (used for display purposes)
    Returns: (hit: bool, reasons: list)
    """
    hits = []

    # Support labels (valid for Bullish BOS)
    support_keywords = ["Support", "Demand", "Low", "Psychological"]
    # Resistance labels (valid for Bearish BOS)
    resistance_keywords = ["Resistance", "Supply", "High", "Psychological"]

    for lvl in htf_levels:
        if lvl["abs_distance_pct"] > threshold_pct:
            continue

        label = lvl["label"]
        direction = "above" if current_price > lvl["price"] else "at/below"

        # Directional filter
        if bos_direction == "Bullish":
            # Price should be at/above a support or demand level
            is_support = any(k in label for k in support_keywords)
            if not is_support:
                continue
        elif bos_direction == "Bearish":
            # Price should be at/below a resistance or supply level
            is_resistance = any(k in label for k in resistance_keywords)
            if not is_resistance:
                continue
        # bos_direction == "None" → accept all

        hits.append(
            f"{lvl['timeframe']} {lvl['label']} @ {lvl['price']:.4f} "
            f"({direction}, {lvl['abs_distance_pct']:.2f}% away)"
        )

    # EMA check — directional
    ema20_pct = abs(current_price - ema20) / ema20 * 100
    ema50_pct = abs(current_price - ema50) / ema50 * 100

    if ema20_pct <= 0.5:
        side = "above" if current_price > ema20 else "at/below"
        # Bullish BOS: EMA valid if price is bouncing off EMA from above (support)
        # Bearish BOS: EMA valid if price is rejecting EMA from below (resistance)
        if bos_direction == "Bullish" and current_price >= ema20 * 0.997:
            hits.append(f"Daily 20 EMA @ {ema20:.4f} ({side}, {ema20_pct:.2f}% away) — support")
        elif bos_direction == "Bearish" and current_price <= ema20 * 1.003:
            hits.append(f"Daily 20 EMA @ {ema20:.4f} ({side}, {ema20_pct:.2f}% away) — resistance")
        elif bos_direction == "None":
            hits.append(f"Daily 20 EMA @ {ema20:.4f} ({side}, {ema20_pct:.2f}% away)")

    if ema50_pct <= 0.8:
        side = "above" if current_price > ema50 else "at/below"
        if bos_direction == "Bullish" and current_price >= ema50 * 0.997:
            hits.append(f"Daily 50 EMA @ {ema50:.4f} ({side}, {ema50_pct:.2f}% away) — support")
        elif bos_direction == "Bearish" and current_price <= ema50 * 1.003:
            hits.append(f"Daily 50 EMA @ {ema50:.4f} ({side}, {ema50_pct:.2f}% away) — resistance")
        elif bos_direction == "None":
            hits.append(f"Daily 50 EMA @ {ema50:.4f} ({side}, {ema50_pct:.2f}% away)")

    # S/D zone directional check (separate from HTF levels)
    return len(hits) > 0, hits


# ── BOS + TFCOT Detection ────────────────────────────────────────────────────────
def detect_bos_tfcot(df_4h, df_2h):
    """
    Two-stage check:
    Stage 1 — 4H BOS: price breaks above/below a prior 4H swing high/low (lookback 60 bars)
    Stage 2 — 2H TFCOT: scan entire 2H history for a confirmed TFCOT that:
               a) occurred AFTER the 4H BOS
               b) has NOT been invalidated by a subsequent lower low (bull) or higher high (bear)
               c) is still active — i.e. the pullback is in progress, not a new BOS
    This catches TFCOT events from days ago that are still valid.
    Returns: direction, bos_detail, tfcot_detail
    """
    bos_direction  = "None"
    bos_detail     = "No clear BOS in recent 4H structure"
    tfcot_detail   = "No 2H TFCOT confirmed"
    bos_swing_low  = None
    bos_swing_high = None
    impulse_low    = None  # 2H TFCOT: higher low (bullish) or swing low broken (bearish)
    impulse_high   = None  # 2H TFCOT: swing high broken (bullish) or lower high (bearish)

    # ── Stage 1: 4H BOS (extended lookback to 60 bars ~10 days)
    if df_4h is not None and len(df_4h) >= 20:
        highs, lows = find_pivot_highs_lows(df_4h.tail(60), left=3, right=3)
        last_high  = float(df_4h['High'].iloc[-1])
        last_low   = float(df_4h['Low'].iloc[-1])
        if len(highs) >= 2:
            prior_h = highs[-2][1]
            if last_high > prior_h:
                bos_direction  = "Bullish"
                bos_detail     = f"4H BOS — broke above swing high @ {prior_h:.4f}"
                # Fib: from most recent swing low up to current 4H high
                bos_swing_low  = lows[-1][1] if lows else None
                bos_swing_high = last_high

        if len(lows) >= 2:
            prior_l = lows[-2][1]
            if last_low < prior_l:
                bos_direction  = "Bearish"
                bos_detail     = f"4H BOS — broke below swing low @ {prior_l:.4f}"
                # Fib: from current 4H low up to most recent swing high
                bos_swing_low  = last_low
                bos_swing_high = highs[-1][1] if highs else None

    # ── Stage 2: 2H TFCOT — scan full 2H history for active confirmation
    if bos_direction != "None" and df_2h is not None and len(df_2h) >= 16:
        highs_2h, lows_2h = find_pivot_highs_lows(df_2h, left=2, right=2)
        current_close = float(df_2h['Close'].iloc[-1])
        current_low   = float(df_2h['Low'].iloc[-1])
        current_high  = float(df_2h['High'].iloc[-1])

        if bos_direction == "Bullish" and len(lows_2h) >= 2 and len(highs_2h) >= 1:
            # Scan all pairs of consecutive 2H lows for a higher low
            tfcot_confirmed = False
            tfcot_confirmed_low = None
            tfcot_confirmed_high_break = None

            for k in range(len(lows_2h) - 1):
                prior_low_idx  = lows_2h[k][0]
                prior_low_val  = lows_2h[k][1]
                recent_low_idx = lows_2h[k+1][0]
                recent_low_val = lows_2h[k+1][1]

                if recent_low_val <= prior_low_val:
                    continue  # not a higher low

                # Find highest 2H swing high between the two lows
                highs_between = [h for h in highs_2h if prior_low_idx < h[0] < recent_low_idx]
                if not highs_between:
                    highs_between = [h for h in highs_2h if h[0] <= recent_low_idx]
                if not highs_between:
                    continue
                swing_high_val = max(h[1] for h in highs_between)
                swing_high_idx = max((h for h in highs_between), key=lambda x: x[1])[0]

                # Check if price ever broke above that swing high after the higher low
                post_low = df_2h.loc[recent_low_idx:]
                if post_low.empty:
                    continue
                if float(post_low['High'].max()) > swing_high_val:
                    # TFCOT confirmed — now check it hasn't been invalidated
                    # Invalidated if a subsequent 2H low goes below recent_low_val
                    future_lows_after = [l for l in lows_2h if l[0] > recent_low_idx]
                    invalidated = any(l[1] < recent_low_val for l in future_lows_after)
                    # Also check current price hasn't broken below the higher low
                    if current_low < recent_low_val * 0.998:
                        invalidated = True

                    if not invalidated:
                        tfcot_confirmed = True
                        tfcot_confirmed_low = recent_low_val
                        tfcot_confirmed_high_break = swing_high_val
                        # Don't break — keep scanning for the most recent valid TFCOT

            if tfcot_confirmed:
                tfcot_detail = (f"2H TFCOT ✅ — Higher low @ {tfcot_confirmed_low:.4f} "
                                f"+ broke 2H high @ {tfcot_confirmed_high_break:.4f} "
                                f"(active, structure intact)")
                # Return 2H impulse swing: anchor=higher low, top=swing high that was broken
                impulse_low  = tfcot_confirmed_low
                impulse_high = tfcot_confirmed_high_break
            else:
                # Check if partial (higher low exists but no break yet)
                recent_lows_sorted = sorted(lows_2h, key=lambda x: x[0])
                if len(recent_lows_sorted) >= 2:
                    pl = recent_lows_sorted[-2][1]
                    rl = recent_lows_sorted[-1][1]
                    # Find next swing high after recent low
                    next_highs = [h for h in highs_2h if h[0] > recent_lows_sorted[-1][0]]
                    if rl > pl and next_highs:
                        next_h = next_highs[0][1]
                        tfcot_detail = (f"2H TFCOT partial — Higher low @ {rl:.4f}, "
                                        f"awaiting break of 2H high @ {next_h:.4f}")
                    elif rl <= pl:
                        tfcot_detail = "2H TFCOT pending — no higher low confirmed yet"

        elif bos_direction == "Bearish" and len(highs_2h) >= 2 and len(lows_2h) >= 1:
            tfcot_confirmed = False
            tfcot_confirmed_high = None
            tfcot_confirmed_low_break = None

            for k in range(len(highs_2h) - 1):
                prior_high_idx  = highs_2h[k][0]
                prior_high_val  = highs_2h[k][1]
                recent_high_idx = highs_2h[k+1][0]
                recent_high_val = highs_2h[k+1][1]

                if recent_high_val >= prior_high_val:
                    continue  # not a lower high

                lows_between = [l for l in lows_2h if prior_high_idx < l[0] < recent_high_idx]
                if not lows_between:
                    lows_between = [l for l in lows_2h if l[0] <= recent_high_idx]
                if not lows_between:
                    continue
                swing_low_val = min(l[1] for l in lows_between)

                post_high = df_2h.loc[recent_high_idx:]
                if post_high.empty:
                    continue
                if float(post_high['Low'].min()) < swing_low_val:
                    future_highs_after = [h for h in highs_2h if h[0] > recent_high_idx]
                    invalidated = any(h[1] > recent_high_val for h in future_highs_after)
                    if current_high > recent_high_val * 1.002:
                        invalidated = True

                    if not invalidated:
                        tfcot_confirmed = True
                        tfcot_confirmed_high = recent_high_val
                        tfcot_confirmed_low_break = swing_low_val

            if tfcot_confirmed:
                tfcot_detail = (f"2H TFCOT ✅ — Lower high @ {tfcot_confirmed_high:.4f} "
                                f"+ broke 2H low @ {tfcot_confirmed_low_break:.4f} "
                                f"(active, structure intact)")
                # 2H impulse: anchor=lower high, bottom=swing low that was broken
                impulse_low  = tfcot_confirmed_low_break
                impulse_high = tfcot_confirmed_high
            else:
                recent_highs_sorted = sorted(highs_2h, key=lambda x: x[0])
                if len(recent_highs_sorted) >= 2:
                    ph = recent_highs_sorted[-2][1]
                    rh = recent_highs_sorted[-1][1]
                    next_lows = [l for l in lows_2h if l[0] > recent_highs_sorted[-1][0]]
                    if rh < ph and next_lows:
                        next_l = next_lows[0][1]
                        tfcot_detail = (f"2H TFCOT partial — Lower high @ {rh:.4f}, "
                                        f"awaiting break of 2H low @ {next_l:.4f}")
                    elif rh >= ph:
                        tfcot_detail = "2H TFCOT pending — no lower high confirmed yet"

    return bos_direction, bos_detail, tfcot_detail, bos_swing_low, bos_swing_high, impulse_low, impulse_high


# ── Precision Levels ─────────────────────────────────────────────────────────────
def compute_fib_levels(swing_low, swing_high):
    diff = swing_high - swing_low
    return {
        "0.0":   round(swing_high, 4),
        "0.236": round(swing_high - 0.236 * diff, 4),
        "0.382": round(swing_high - 0.382 * diff, 4),
        "0.500": round(swing_high - 0.500 * diff, 4),
        "0.618": round(swing_high - 0.618 * diff, 4),
        "0.786": round(swing_high - 0.786 * diff, 4),
        "1.0":   round(swing_low, 4),
    }


def compute_avwap(df, anchor_price=None, anchor_bars=20):
    """Compute AVWAP anchored to the bar closest to anchor_price (BOS swing).
    Falls back to highest-volume bar in last anchor_bars if no anchor provided."""
    if 'Volume' not in df.columns or df['Volume'].sum() == 0:
        return None
    if anchor_price is not None:
        # Find the bar whose Low is closest to anchor price (works for both bullish higher low
        # and bearish impulse high since we pass the appropriate level)
        low_dist  = (df['Low']  - anchor_price).abs()
        high_dist = (df['High'] - anchor_price).abs()
        combined  = low_dist.combine(high_dist, min)
        anchor_pos = int(combined.argmin())
    else:
        recent = df.tail(anchor_bars).copy()
        if recent.empty:
            return None
        anchor_idx = recent['Volume'].idxmax()
        anchor_pos = df.index.get_loc(anchor_idx)
    sub = df.iloc[anchor_pos:].copy()
    sub = sub.dropna(subset=['High','Low','Close','Volume'])  # drop incomplete bars
    if sub.empty or sub['Volume'].astype(float).sum() == 0:
        return None
    sub['TP']  = (sub['High'] + sub['Low'] + sub['Close']) / 3
    sub['TPV'] = sub['TP'] * sub['Volume'].astype(float)
    avwap = (sub['TPV'].cumsum() / sub['Volume'].astype(float).cumsum()).iloc[-1]
    v = float(avwap)
    return round(v, 4) if v == v else None  # guard against any remaining NaN


def compute_fixed_vol_poc(df, bins=30):
    if 'Volume' not in df.columns or df['Volume'].sum() == 0:
        return None
    recent = df.tail(60).copy()
    prices = (recent['High'] + recent['Low']) / 2
    p_min, p_max = prices.min(), prices.max()
    if p_max == p_min:
        return None
    price_bins  = np.linspace(p_min, p_max, bins + 1)
    vol_by_bin  = np.zeros(bins)
    for _, row in recent.iterrows():
        mid = (row['High'] + row['Low']) / 2
        if mid != mid:  # skip NaN
            continue
        idx = min(int((mid - p_min) / (p_max - p_min) * bins), bins - 1)
        vol_by_bin[idx] += row['Volume']
    poc_bin = np.argmax(vol_by_bin)
    poc_price = (price_bins[poc_bin] + price_bins[poc_bin + 1]) / 2
    return round(float(poc_price), 4)


# ── Institutional Supply & Demand Zone Detection ────────────────────────────────
def detect_sd_zones(df_daily, df_weekly, current_price, lookback=120):
    """
    Detect institutional supply and demand zones using:
    - Rally-Base-Drop  (RBD) → Supply zone (bearish)
    - Drop-Base-Rally  (DBR) → Demand zone (bullish)
    - Rally-Base-Rally (RBR) → Continuation demand
    - Drop-Base-Drop   (DBD) → Continuation supply

    A 'base' = 1-3 consecutive small-range candles after a strong impulse.
    The zone is the price range of the base candle(s).
    Returns list of dicts: {type, top, bottom, mid, strength, timeframe, distance_pct}
    """
    zones = []

    def find_zones_in_df(df, tf_label, min_impulse_pct=0.4, max_base_range_pct=0.35):
        if df is None or len(df) < 10:
            return []
        tf_zones = []
        df = df.copy().tail(lookback)
        closes = df['Close'].values.astype(float)
        highs  = df['High'].values.astype(float)
        lows   = df['Low'].values.astype(float)
        n = len(df)

        for i in range(2, n - 3):
            body_pct = lambda j: abs(closes[j] - df['Open'].values[j]) / lows[j] * 100
            range_pct = lambda j: (highs[j] - lows[j]) / lows[j] * 100

            # Identify base candle: small range relative to neighbours
            is_base = range_pct(i) <= max_base_range_pct
            if not is_base:
                continue

            # Look left for impulse candle
            prev_range  = range_pct(i - 1)
            prev_is_bull = closes[i-1] > df['Open'].values[i-1]
            prev_is_bear = closes[i-1] < df['Open'].values[i-1]

            # Look right for departure candle
            if i + 1 >= n:
                continue
            next_range   = range_pct(i + 1)
            next_is_bull = closes[i+1] > df['Open'].values[i+1]
            next_is_bear = closes[i+1] < df['Open'].values[i+1]

            # Impulse must be strong (large range)
            strong_impulse = prev_range >= min_impulse_pct
            strong_departure = next_range >= min_impulse_pct

            if not (strong_impulse and strong_departure):
                continue

            zone_top    = round(highs[i], 4)
            zone_bottom = round(lows[i],  4)
            zone_mid    = round((zone_top + zone_bottom) / 2, 4)

            # Classify pattern
            zone_type = None
            if prev_is_bull and next_is_bear:    # Rally → Base → Drop
                zone_type = "Supply (RBD)"
            elif prev_is_bear and next_is_bull:  # Drop → Base → Rally
                zone_type = "Demand (DBR)"
            elif prev_is_bull and next_is_bull:  # Rally → Base → Rally
                zone_type = "Demand (RBR)"
            elif prev_is_bear and next_is_bear:  # Drop → Base → Drop
                zone_type = "Supply (DBD)"

            if zone_type is None:
                continue

            # Strength: how far price has moved away (bigger departure = stronger zone)
            strength_r = round(next_range / max(range_pct(i), 0.01), 1)
            strength   = "Strong" if strength_r >= 3 else ("Moderate" if strength_r >= 1.5 else "Weak")

            # Only keep untested zones (price hasn't returned to zone since formation)
            future_lows  = lows[i+2:]  if i+2 < n else np.array([])
            future_highs = highs[i+2:] if i+2 < n else np.array([])
            if len(future_lows) > 0:
                if "Supply" in zone_type:
                    # Supply zone is tested if price traded into it from below
                    if any(future_highs >= zone_bottom):
                        continue  # zone has been tested/violated
                else:
                    if any(future_lows <= zone_top):
                        continue  # demand zone tested

            dist_pct = round((zone_mid - current_price) / current_price * 100, 2)
            tf_zones.append({
                "type":         zone_type,
                "top":          zone_top,
                "bottom":       zone_bottom,
                "mid":          zone_mid,
                "strength":     strength,
                "timeframe":    tf_label,
                "distance_pct": dist_pct,
                "abs_dist":     abs(dist_pct),
            })
        return tf_zones

    zones += find_zones_in_df(df_daily,  "Daily",  min_impulse_pct=0.4, max_base_range_pct=0.35)
    zones += find_zones_in_df(df_weekly, "Weekly", min_impulse_pct=0.6, max_base_range_pct=0.50)

    # Deduplicate zones within 0.5% of each other (keep strongest)
    zones.sort(key=lambda z: z["abs_dist"])
    deduped = []
    for z in zones:
        overlap = False
        for d in deduped:
            if abs(z["mid"] - d["mid"]) / d["mid"] * 100 <= 0.5:
                # Keep the stronger / higher TF one
                if z["strength"] == "Strong" and d["strength"] != "Strong":
                    deduped.remove(d)
                    deduped.append(z)
                overlap = True
                break
        if not overlap:
            deduped.append(z)

    deduped.sort(key=lambda z: z["abs_dist"])
    return deduped[:12]  # return closest 12 zones


# ── Options / Gamma Levels ────────────────────────────────────────────────────────
OPTIONS_MAP = {
    "EURUSD": {"options_ticker": None,   "price_multiplier": 1.0},
    "US500":  {"options_ticker": "^SPX", "price_multiplier": 1.0},
    "XAUUSD": {"options_ticker": "GLD",  "price_multiplier": 10.0},
    "XAGUSD": {"options_ticker": "SLV",  "price_multiplier": 1.0},
    "BTCUSD": {"options_ticker": None,   "price_multiplier": 1.0},
    "HK50":   {"options_ticker": None,   "price_multiplier": 1.0},
}

def get_options_levels(symbol, current_price, n_expiries=6):
    """
    Fetch options OI to derive:
    - Top call OI strikes (dealer resistance / gamma walls above)
    - Top put OI strikes (dealer support / gamma walls below)
    - Max pain level
    - Put/Call ratio (sentiment)
    Returns dict of levels or None.
    """
    opts_info = OPTIONS_MAP.get(symbol, {})
    opts_ticker = opts_info.get("options_ticker")
    multiplier  = opts_info.get("price_multiplier", 1.0)

    if not opts_ticker:
        return None

    try:
        t   = yf.Ticker(opts_ticker)
        exp = t.options
        if not exp:
            return None

        all_calls, all_puts = [], []
        for e in exp[:n_expiries]:
            try:
                chain = t.option_chain(e)
                c = chain.calls.copy(); c['expiry'] = e
                p = chain.puts.copy();  p['expiry'] = e
                all_calls.append(c)
                all_puts.append(p)
            except:
                pass

        if not all_calls:
            return None

        calls = pd.concat(all_calls, ignore_index=True)
        puts  = pd.concat(all_puts,  ignore_index=True)

        # Convert ETF strikes to underlying price if needed
        calls['strike_actual'] = calls['strike'] * multiplier
        puts['strike_actual']  = puts['strike']  * multiplier

        # Aggregate OI by actual strike
        calls_oi = calls.groupby('strike_actual')['openInterest'].sum()
        puts_oi  = puts.groupby('strike_actual')['openInterest'].sum()

        # Near the money ±15%
        lo, hi = current_price * 0.85, current_price * 1.15
        calls_near = calls_oi[(calls_oi.index >= lo) & (calls_oi.index <= hi)]
        puts_near  = puts_oi[(puts_oi.index >= lo)  & (puts_oi.index <= hi)]

        # Top 4 each
        top_calls = calls_near.nlargest(4)
        top_puts  = puts_near.nlargest(4)

        # Max pain (strike with highest combined OI)
        combined = pd.merge(
            calls_oi.reset_index().rename(columns={'strike_actual':'strike','openInterest':'c_oi'}),
            puts_oi.reset_index().rename(columns={'strike_actual':'strike','openInterest':'p_oi'}),
            on='strike', how='outer'
        ).fillna(0)
        combined['total'] = combined['c_oi'] + combined['p_oi']
        max_pain = float(combined.loc[combined['total'].idxmax(), 'strike'])

        # Put/Call ratio (total OI)
        total_call_oi = int(calls['openInterest'].sum())
        total_put_oi  = int(puts['openInterest'].sum())
        pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else None
        sentiment = "Bearish" if pcr and pcr > 1.2 else ("Bullish" if pcr and pcr < 0.8 else "Neutral")

        # Gamma wall above (highest call OI above spot)
        calls_above = calls_near[calls_near.index > current_price]
        calls_below = calls_near[calls_near.index < current_price]
        puts_above  = puts_near[puts_near.index > current_price]
        puts_below  = puts_near[puts_near.index < current_price]

        gamma_wall_above = float(calls_above.idxmax()) if not calls_above.empty else None
        gamma_wall_below = float(puts_below.idxmax())  if not puts_below.empty else None

        return {
            "top_call_strikes": [(round(float(s),2), int(oi)) for s, oi in top_calls.items()],
            "top_put_strikes":  [(round(float(s),2), int(oi)) for s, oi in top_puts.items()],
            "max_pain":         round(max_pain, 2),
            "gamma_wall_above": round(gamma_wall_above, 2) if gamma_wall_above else None,
            "gamma_wall_below": round(gamma_wall_below, 2) if gamma_wall_below else None,
            "pcr":              pcr,
            "sentiment":        sentiment,
            "source":           opts_ticker,
            "total_call_oi":    total_call_oi,
            "total_put_oi":     total_put_oi,
        }
    except Exception as e:
        return None


# ── Scoring ──────────────────────────────────────────────────────────────────────
def score_setup(location_hit, bos, precision_hit):
    score = sum([location_hit, bos != "None", precision_hit])
    if score == 3:
        return "🟢 A+ SETUP", score
    elif score == 2:
        return "🟡 B SETUP – Monitor", score
    elif score == 1:
        return "🟠 WATCHING – 1/3 criteria", score
    else:
        return "🔴 NO SETUP", score


# ── Main Scanner ─────────────────────────────────────────────────────────────────
def scan_instrument(symbol, info):
    ticker = info["ticker"]
    result = {"symbol": symbol, "name": info["name"], "ticker": ticker, "error": None}

    df_daily   = fetch_data(ticker, period="1y",  interval="1d")
    df_weekly  = fetch_weekly(ticker)
    df_monthly = fetch_monthly(ticker)
    df_4h      = fetch_4h(ticker)
    df_2h      = fetch_2h(ticker)

    if df_daily is None or len(df_daily) < 55:
        result["error"] = "Insufficient daily data"
        return result

    # Use live 1H price as current price (avoids daily close lag during session)
    live_price = None
    try:
        df_live = fetch_data(ticker, period="2d", interval="1h")
        if df_live is not None and not df_live.empty:
            live_series = df_live['Close'].dropna()
            if not live_series.empty:
                live_price = float(live_series.iloc[-1])
    except Exception:
        pass

    close_series  = df_daily['Close'].dropna()
    daily_close   = float(close_series.iloc[-1]) if not close_series.empty else float('nan')
    prev_close    = float(df_daily['Close'].iloc[-2])
    current_price = live_price if live_price else daily_close

    result["current_price"] = current_price
    result["daily_change_pct"] = round(
        (current_price - prev_close) / prev_close * 100, 2
    ) if prev_close else 0.0
    result["daily_high"] = round(float(df_daily['High'].dropna().iloc[-1]), 4)
    result["daily_low"]  = round(float(df_daily['Low'].dropna().iloc[-1]), 4)

    # EMA
    closes = df_daily['Close'].astype(float)
    ema20  = float(ema(closes, 20).iloc[-1])
    ema50  = float(ema(closes, 50).iloc[-1])
    result["ema20"] = round(ema20, 4)
    result["ema50"] = round(ema50, 4)

    # Two-tier bias
    if current_price > ema20 and current_price > ema50 and ema20 > ema50:
        trend_bias = "⬆⬆ Strong Bull"
    elif current_price > ema50 and current_price < ema20:
        trend_bias = "⬆  Bullish"
    elif current_price < ema50 and current_price > ema20:
        trend_bias = "⬇  Bearish"
    else:
        trend_bias = "⬇⬇ Strong Bear"
    result["trend_bias"] = trend_bias

    # HTF Levels
    htf_levels = detect_htf_levels(df_daily, df_weekly, df_monthly, current_price)
    result["htf_levels"] = htf_levels

    # BOS (4H) + TFCOT (2H) — run first so direction feeds into location check
    bos_direction, bos_detail, tfcot_detail, bos_swing_low, bos_swing_high, impulse_low, impulse_high = detect_bos_tfcot(df_4h, df_2h)

    # Location check — directional filter applied based on BOS
    location_hit, location_reasons = check_location(
        current_price, htf_levels, ema20, ema50, bos_direction=bos_direction
    )
    result["location_hit"]     = location_hit
    result["location_reasons"] = location_reasons
    result["bos_direction"]    = bos_direction
    result["bos_detail"]       = bos_detail
    result["tfcot_detail"]     = tfcot_detail

    # Precision — use 2H TFCOT impulse swing (most precise), fall back to 4H BOS swing, then 20-day
    if impulse_low and impulse_high and impulse_high > impulse_low:
        # 2H confirmed: fib measured from impulse low to impulse high
        swing_low  = impulse_low
        swing_high = impulse_high
    elif bos_swing_low and bos_swing_high and bos_swing_high > bos_swing_low:
        swing_low  = bos_swing_low
        swing_high = bos_swing_high
    else:
        recent_20  = df_daily.tail(20)
        swing_low  = float(recent_20['Low'].min())
        swing_high = float(recent_20['High'].max())
    fibs = compute_fib_levels(swing_low, swing_high)
    result["fib_levels"] = fibs
    fib_618 = fibs["0.618"]

    # AVWAP anchored to 2H impulse start (higher low for bullish, lower high for bearish)
    # Use df_2h so the AVWAP reflects 2H price action from that point forward
    if bos_direction == "Bullish" and impulse_low:
        avwap_anchor = impulse_low
        avwap = compute_avwap(df_2h, anchor_price=avwap_anchor)
    elif bos_direction == "Bearish" and impulse_high:
        avwap_anchor = impulse_high
        avwap = compute_avwap(df_2h, anchor_price=avwap_anchor)
    else:
        avwap = compute_avwap(df_daily, anchor_price=None)
    poc   = compute_fixed_vol_poc(df_2h if df_2h is not None and len(df_2h) > 10 else df_daily, bins=20)
    result["avwap"] = avwap
    result["poc"]   = poc

    precision_items = [("0.618 Fib", fib_618)]
    if avwap: precision_items.append(("AVWAP", avwap))
    if poc:   precision_items.append(("Vol POC", poc))

    near_precision = []
    fib_hit = False
    for label, level in precision_items:
        if level:
            pct = abs(current_price - level) / level * 100
            if pct <= 0.6:
                direction = "below" if current_price < level else "above"
                near_precision.append(f"{label} @ {level:.4f} ({direction}, {pct:.2f}% away)")
                if label == "0.618 Fib":
                    fib_hit = True

    # Precision only triggers if price is at the 0.618 fib.
    # AVWAP and POC are supporting confluence, not standalone triggers.
    result["near_precision"] = near_precision
    result["precision_hit"]  = fib_hit
    result["precision_items"] = precision_items

    # S/D Zones
    sd_zones = detect_sd_zones(df_daily, df_weekly, current_price)
    result["sd_zones"] = sd_zones

    # Options / Gamma Levels
    gamma = get_options_levels(symbol, current_price)
    result["gamma"] = gamma

    # Score
    rating, score = score_setup(location_hit, bos_direction, result["precision_hit"])
    result["rating"] = rating
    result["score"]  = score

    return result


# ── Report Generator ─────────────────────────────────────────────────────────────
def build_report(results):
    now_aest = datetime.now(AEST)
    W = 68
    lines = []

    lines.append("=" * W)
    lines.append("  PROP TRADE STRATEGY SCANNER")
    lines.append(f"  {now_aest.strftime('%A %d %B %Y  |  %I:%M %p AEST')}")
    lines.append("=" * W)
    lines.append("")
    lines.append("CHECKLIST:  1. LOCATION  →  2. BOS (4H) + TFCOT (2H)  →  3. PRECISION")
    lines.append("            HTF zone / 20-50 EMA  |  4H BOS then 2H TFCOT  |  0.618 / POC / AVWAP")
    lines.append("")
    lines.append("BIAS KEY:")
    lines.append("  ⬆⬆ Strong Bull — Price above 20 & 50 EMA, 20 EMA above 50 EMA (uptrend)")
    lines.append("  ⬆  Bullish     — Price above 50 EMA but below 20 EMA (pullback in uptrend)")
    lines.append("  ⬇  Bearish     — Price below 50 EMA but above 20 EMA (pullback in downtrend)")
    lines.append("  ⬇⬇ Strong Bear — Price below 20 & 50 EMA, 20 EMA below 50 EMA (downtrend)")
    lines.append("")

    order = {"🟢 A+ SETUP": 0, "🟡 B SETUP – Monitor": 1,
             "🟠 WATCHING – 1/3 criteria": 2, "🔴 NO SETUP": 3}
    sorted_results = sorted(results, key=lambda r: order.get(r.get("rating", "🔴 NO SETUP"), 4))

    for r in sorted_results:
        lines.append("─" * W)
        sym  = r["symbol"]
        name = r["name"]

        if r.get("error"):
            lines.append(f"  {sym} ({name})  ⚠️  {r['error']}")
            continue

        price  = r["current_price"]
        chg    = r["daily_change_pct"]
        rating = r["rating"]
        bias   = r["trend_bias"]
        chg_str = f"+{chg}%" if chg >= 0 else f"{chg}%"

        direction_label = "📈 LONG" if r["bos_direction"] == "Bullish" else ("📉 SHORT" if r["bos_direction"] == "Bearish" else "⏳ NO DIRECTION")
        lines.append(f"  {sym}  |  {name}  |  {price:.4f}  ({chg_str})  |  Bias: {bias}  |  {direction_label}")
        lines.append(f"  {rating}")
        lines.append(f"  Day Range: {r['daily_low']:.4f} – {r['daily_high']:.4f}"
                     f"  |  EMA20: {r['ema20']:.4f}  |  EMA50: {r['ema50']:.4f}")
        lines.append("")

        # ── CRITERIA
        loc_icon  = "✅" if r["location_hit"]  else "❌"
        bos_icon  = "✅" if r["bos_direction"] != "None" else "❌"
        prec_icon = "✅" if r["precision_hit"] else "❌"

        # 1. LOCATION
        lines.append(f"  {loc_icon} 1. LOCATION")
        if r["location_reasons"]:
            for reason in r["location_reasons"]:
                lines.append(f"        ★ {reason}")
        else:
            lines.append("        → No zone touch detected")

        # 2. BOS
        lines.append(f"  {bos_icon} 2. BOS (4H) + TFCOT (2H)")
        lines.append(f"        → {r['bos_detail']}")
        lines.append(f"        → {r.get('tfcot_detail', 'No 2H TFCOT data')}")

        # 3. PRECISION
        lines.append(f"  {prec_icon} 3. PRECISION")
        fibs = r["fib_levels"]
        avwap = r.get("avwap")
        poc   = r.get("poc")
        lines.append(f"        → 0.618 Fib: {fibs['0.618']:.4f}"
                     f"   AVWAP: {avwap if avwap else 'N/A'}"
                     f"   Vol POC: {poc if poc else 'N/A'}")
        if r["near_precision"]:
            for p in r["near_precision"]:
                lines.append(f"        ★ NEAR: {p}")

        lines.append("")

        # ── HTF LEVELS TABLE
        lines.append("  HTF KEY LEVELS  (sorted by proximity to current price)")
        lines.append(f"  {'Level':>10}   {'Type':<35}  {'TF':<8}  {'Distance':>8}")
        lines.append("  " + "-" * 62)

        htf = r.get("htf_levels", [])
        # Show levels above and below (up to 6 above, 6 below)
        above = [l for l in htf if l["price"] > price][:6]
        below = [l for l in htf if l["price"] <= price][-6:]
        combined = above + below
        combined.sort(key=lambda x: x["abs_distance_pct"])

        for lvl in combined[:12]:
            direction = "▲" if lvl["price"] > price else "▼"
            dist_str  = f"{direction}{abs(lvl['distance_pct']):.2f}%"
            tag = " ★" if lvl["abs_distance_pct"] <= 0.6 else ""
            lines.append(
                f"  {lvl['price']:>10.4f}   {lvl['label']:<35}  {lvl['timeframe']:<8}  {dist_str:>8}{tag}"
            )

        # ── S/D ZONES
        lines.append("")
        sd_zones = r.get("sd_zones", [])
        if sd_zones:
            lines.append("  INSTITUTIONAL S/D ZONES  (untested, sorted by proximity)")
            lines.append(f"  {'Zone Mid':>10}   {'Range':<22}  {'Type':<16}  {'TF':<7}  {'Str':<8}  {'Dist':>7}")
            lines.append("  " + "-" * 72)
            supply_zones = [z for z in sd_zones if "Supply" in z["type"]]
            demand_zones = [z for z in sd_zones if "Demand" in z["type"]]
            # Show 3 above (supply) and 3 below (demand) closest to price
            supply_sorted = sorted(supply_zones, key=lambda z: z["abs_dist"])[:4]
            demand_sorted = sorted(demand_zones, key=lambda z: z["abs_dist"])[:4]
            for z in sorted(supply_sorted + demand_sorted, key=lambda z: z["abs_dist"]):
                direction = "▲" if z["distance_pct"] > 0 else "▼"
                dist_str  = f"{direction}{z['abs_dist']:.2f}%"
                tag = " ★" if z["abs_dist"] <= 1.0 else ""
                zone_icon = "🔴" if "Supply" in z["type"] else "🟢"
                lines.append(
                    f"  {z['mid']:>10.4f}   {z['bottom']:.2f}–{z['top']:.2f}   "
                    f"  {zone_icon} {z['type']:<14}  {z['timeframe']:<7}  {z['strength']:<8}  {dist_str:>7}{tag}"
                )
        else:
            lines.append("  INSTITUTIONAL S/D ZONES: No untested zones detected nearby")

        # ── GAMMA / OPTIONS LEVELS
        lines.append("")
        gamma = r.get("gamma")
        if gamma:
            pcr_str = f"{gamma['pcr']} ({gamma['sentiment']})" if gamma.get('pcr') else "N/A"
            lines.append(f"  OPTIONS / GAMMA LEVELS  (source: {gamma['source']})  PCR: {pcr_str}")
            lines.append(f"  Max Pain: {gamma['max_pain']:.2f}   "
                         f"Gamma Wall ▲: {gamma['gamma_wall_above'] if gamma['gamma_wall_above'] else 'N/A'}   "
                         f"Gamma Wall ▼: {gamma['gamma_wall_below'] if gamma['gamma_wall_below'] else 'N/A'}")
            lines.append("")
            lines.append(f"  {'Strike':>10}   {'Side':<12}  {'OI':>10}   {'vs Spot':>8}")
            lines.append("  " + "-" * 44)
            all_opts = [(s, oi, "Call (R)") for s, oi in gamma["top_call_strikes"]] + \
                       [(s, oi, "Put  (S)") for s, oi in gamma["top_put_strikes"]]
            all_opts.sort(key=lambda x: abs(x[0] - price))
            for strike, oi, side in all_opts:
                dist_pct = (strike - price) / price * 100
                direction = "▲" if strike > price else "▼"
                tag = " ← near" if abs(dist_pct) <= 1.5 else ""
                lines.append(
                    f"  {strike:>10.2f}   {side:<12}  {oi:>10,}   {direction}{abs(dist_pct):.2f}%{tag}"
                )
        else:
            lines.append("  OPTIONS / GAMMA LEVELS: Not available for this instrument")

    lines.append("─" * W)
    lines.append("")

    # ── SUMMARY TABLE
    lines.append("SUMMARY")
    lines.append(f"  {'Symbol':<10} {'Price':>10} {'Chg':>7} {'Bias':<14} {'Direction':<12} {'Score':>6}  Rating")
    lines.append("  " + "-" * 72)
    for r in sorted_results:
        if r.get("error"):
            lines.append(f"  {r['symbol']:<10} ERROR")
            continue
        chg = r["daily_change_pct"]
        chg_str = f"+{chg}%" if chg >= 0 else f"{chg}%"
        direction_label = "📈 LONG" if r["bos_direction"] == "Bullish" else ("📉 SHORT" if r["bos_direction"] == "Bearish" else "— none")
        lines.append(
            f"  {r['symbol']:<10} {r['current_price']:>10.4f} {chg_str:>7} "
            f"{r['trend_bias']:<14} {direction_label:<12} {r['score']}/3     {r['rating']}"
        )

    lines.append("")
    lines.append("=" * W)
    lines.append("  RULE: ALL 3 criteria YES before entry.")
    lines.append("  Risk: max 0.5%  |  R/R: 2:1+  |  No checklist = No trade.")
    lines.append("=" * W)
    lines.append("")
    return "\n".join(lines)


# ── Entry Point ───────────────────────────────────────────────────────────────────
def run_scanner():
    print("Running Prop Trade Strategy Scanner...")
    results = []
    for symbol, info in INSTRUMENTS.items():
        print(f"  Scanning {symbol}...")
        results.append(scan_instrument(symbol, info))

    report = build_report(results)

    REPORTS_DIR = os.path.expanduser("~/Documents/scanner_reports")
    os.makedirs(REPORTS_DIR, exist_ok=True)
    now   = datetime.now(AEST)
    fname = os.path.join(REPORTS_DIR, f"scan_{now.strftime('%Y%m%d_%H%M')}.txt")
    with open(fname, "w") as f:
        f.write(report)
    with open(os.path.join(REPORTS_DIR, "latest.txt"), "w") as f:
        f.write(report)
    with open(os.path.join(REPORTS_DIR, "latest.json"), "w") as f:
        json.dump(results, f, indent=2, default=str)

    print(report)
    return report, fname


if __name__ == "__main__":
    run_scanner()
