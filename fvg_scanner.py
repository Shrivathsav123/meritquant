#!/usr/bin/env python3
"""fvg_scanner.py — Fair Value Gap (FVG) scanner for NYSE stocks.

Detects bullish FVGs across:
  Investing : daily (1d), weekly (1wk), monthly (1mo)
  Intraday  : 5-min (5m), 10-min (resampled from 5m), 15-min (15m)

Scoring uses 8 institutional-grade criteria:
  1. 3-candle gap: high[A] < low[C]
  2. FVG depth: Candle-B body > 1.5× ATR(14)
  3. Retest zone: price touches low[C]; invalidated below 50% equilibrium
  4. RSI filter: retest RSI < 35; weekly RSI > 40 (investing TFs)
  5. Consolidation: mark DEAD at 10+ candles inside gap
  6. Volume: breakout candle > 20-period volume MA
  7. Timeframe alignment: daily + weekly both active → bonus
  8. Stop trigger: close below gap_bottom → DEAD
"""

import json
import os
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
import ta

DATA_DIR    = "data"
FVG_FILE    = f"{DATA_DIR}/fvg_signals.json"
MAX_SIGNALS = 100
MIN_GAP_PCT = 0.001   # 0.1% minimum gap size
ATR_PERIOD  = 14
VOL_PERIOD  = 20
RSI_PERIOD  = 14

os.makedirs(DATA_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
}

# (yahoo_interval, yahoo_period, tf_label, is_intraday)
INVESTING_TFS = [
    ("1d",  "1y",  "daily",   False),
    ("1wk", "5y",  "weekly",  False),
    ("1mo", "10y", "monthly", False),
]

INTRADAY_TFS = [
    ("5m",  "60d", "5min",   True),
    ("15m", "60d", "15min",  True),
]


def _fetch(ticker: str, period: str, interval: str) -> pd.DataFrame:
    try:
        time.sleep(0.3)
        days_map = {
            "5d": 5, "1mo": 30, "3mo": 90, "60d": 60,
            "1y": 365, "2y": 730, "5y": 1825, "10y": 3650,
        }
        end_ts   = int(datetime.now().timestamp())
        days     = days_map.get(period, 365)
        start_ts = int((datetime.now() - timedelta(days=days)).timestamp())
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            f"?period1={start_ts}&period2={end_ts}&interval={interval}"
            f"&includePrePost=false"
        )
        session = requests.Session()
        session.headers.update(HEADERS)
        session.get("https://finance.yahoo.com", timeout=8)
        resp = session.get(url, timeout=12)
        if resp.status_code != 200:
            return pd.DataFrame()
        result = resp.json().get("chart", {}).get("result", [])
        if not result:
            return pd.DataFrame()
        chart      = result[0]
        timestamps = chart.get("timestamp", [])
        quote      = chart.get("indicators", {}).get("quote", [{}])[0]
        if not timestamps:
            return pd.DataFrame()
        df = pd.DataFrame({
            "Open":   quote.get("open",   [None] * len(timestamps)),
            "High":   quote.get("high",   [None] * len(timestamps)),
            "Low":    quote.get("low",    [None] * len(timestamps)),
            "Close":  quote.get("close",  [None] * len(timestamps)),
            "Volume": quote.get("volume", [0]    * len(timestamps)),
        }, index=pd.to_datetime(timestamps, unit="s"))
        return df.dropna(subset=["Close"])
    except Exception:
        return pd.DataFrame()


def _resample_10m(df_5m: pd.DataFrame) -> pd.DataFrame:
    try:
        return df_5m.resample("10min").agg({
            "Open":   "first",
            "High":   "max",
            "Low":    "min",
            "Close":  "last",
            "Volume": "sum",
        }).dropna(subset=["Close"])
    except Exception:
        return pd.DataFrame()


def _atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> np.ndarray:
    try:
        return ta.volatility.AverageTrueRange(
            df["High"], df["Low"], df["Close"], window=period
        ).average_true_range().values
    except Exception:
        tr = pd.concat([
            df["High"] - df["Low"],
            (df["High"] - df["Close"].shift()).abs(),
            (df["Low"]  - df["Close"].shift()).abs(),
        ], axis=1).max(axis=1)
        return tr.rolling(period).mean().values


def _rsi_values(series: pd.Series, period: int = RSI_PERIOD) -> np.ndarray:
    try:
        return ta.momentum.RSIIndicator(series, window=period).rsi().values
    except Exception:
        return np.full(len(series), 50.0)


def _scan_df(df: pd.DataFrame, tf_label: str, weekly_rsi: float = None) -> list:
    """
    Core FVG detection loop across one OHLCV DataFrame.

    Returns a list of FVG dicts (non-dead, non-invalidated only).
    """
    n = len(df)
    if n < ATR_PERIOD + 5:
        return []

    high   = df["High"].values.astype(float)
    low    = df["Low"].values.astype(float)
    close  = df["Close"].values.astype(float)
    open_  = df["Open"].values.astype(float)
    vol    = df["Volume"].values.astype(float)
    dates  = df.index

    atr_arr    = _atr(df)
    rsi_arr    = _rsi_values(df["Close"])
    vol_ma_arr = pd.Series(vol).rolling(VOL_PERIOD).mean().values

    fvgs = []

    for i in range(2, n):
        # ── Rule 1: 3-candle structural imbalance ─────────────────────────
        # Candle A = i-2, Candle B = i-1, Candle C = i
        gap_bottom = high[i - 2]   # top of Candle A
        gap_top    = low[i]        # bottom of Candle C
        if gap_top <= gap_bottom:
            continue

        gap_size = gap_top - gap_bottom
        ref_price = close[i - 2] if close[i - 2] > 0 else 1.0
        if gap_size / ref_price < MIN_GAP_PCT:
            continue               # too small to trade

        equilibrium = (gap_bottom + gap_top) / 2.0

        # ── Rule 2: FVG depth — Candle B body > 1.5× ATR(14) ─────────────
        atr_val  = atr_arr[i - 1] if not np.isnan(atr_arr[i - 1]) else None
        b_body   = abs(close[i - 1] - open_[i - 1])
        depth_ok = bool(atr_val and atr_val > 0 and b_body > 1.5 * atr_val)

        # ── Rule 6: Volume — breakout candle > 20-period MA ───────────────
        vol_ma_val = vol_ma_arr[i - 1]
        vol_ok     = bool(
            not np.isnan(vol_ma_val) and vol_ma_val > 0 and vol[i - 1] > vol_ma_val
        )
        vol_ratio  = round(float(vol[i - 1]) / float(vol_ma_val), 2) \
                     if vol_ma_val and vol_ma_val > 0 and not np.isnan(vol_ma_val) else None

        rsi_at_form = float(rsi_arr[i]) if not np.isnan(rsi_arr[i]) else None

        # ── Rules 3, 5, 8: Trace subsequent candles ───────────────────────
        status         = "FORMED"
        retest_rsi     = None
        candles_in_gap = 0
        dead           = False

        for j in range(i + 1, n):
            in_gap = (low[j] <= gap_top) and (high[j] >= gap_bottom)
            if in_gap:
                candles_in_gap += 1
                if status == "FORMED":
                    status     = "RETEST"
                    retest_rsi = float(rsi_arr[j]) \
                                 if j < len(rsi_arr) and not np.isnan(rsi_arr[j]) else None

            # Rule 5: dead at 10+ candles inside gap
            if candles_in_gap >= 10:
                dead = True
                break

            # Rule 3: invalidated if close below 50% equilibrium
            if close[j] < equilibrium:
                dead = True
                break

            # Rule 8: hard stop — close below gap bottom
            if close[j] < gap_bottom:
                dead = True
                break

        if dead:
            continue

        # Only surface FVGs formed in the last 80 candles (still actionable)
        if i < n - 80:
            continue

        # ── Rule 4: RSI filter scoring ────────────────────────────────────
        rsi_check = retest_rsi if retest_rsi is not None else rsi_at_form
        rsi_oversold_ok = bool(rsi_check is not None and rsi_check < 35)
        weekly_macro_ok = bool(weekly_rsi is not None and weekly_rsi > 40)

        # ── Signal score (0–10) ───────────────────────────────────────────
        score = 0
        if depth_ok:                  score += 2   # Rule 2 — institutional impulse
        if vol_ok:                    score += 2   # Rule 6 — volume-confirmed breakout
        if status == "RETEST":        score += 2   # Rule 3 — active retest = entry zone
        if rsi_oversold_ok:           score += 1   # Rule 4a — oversold on retest
        if weekly_macro_ok:           score += 1   # Rule 4b — macro uptrend intact
        if gap_size / ref_price > 0.01: score += 1 # large structural imbalance
        # Rule 7 multi-TF alignment bonus applied later in run()

        fvgs.append({
            "timeframe":     tf_label,
            "formed_date":   str(dates[i].date()),
            "gap_bottom":    round(float(gap_bottom), 4),
            "gap_top":       round(float(gap_top),    4),
            "gap_midpoint":  round(float(equilibrium), 4),
            "gap_size_pct":  round(gap_size / ref_price * 100, 3),
            "depth_ok":      depth_ok,
            "vol_ok":        vol_ok,
            "vol_ratio":     vol_ratio,
            "rsi_at_retest": round(rsi_check, 1) if rsi_check is not None else None,
            "rsi_oversold":  rsi_oversold_ok,
            "weekly_rsi":    round(weekly_rsi, 1) if weekly_rsi is not None else None,
            "weekly_ok":     weekly_macro_ok,
            "status":        status,
            "candles_in_gap": candles_in_gap,
            "current_price": round(float(close[-1]), 4),
            "stop_loss":     round(float(gap_bottom), 4),
            "signal_score":  score,
        })

    fvgs.sort(key=lambda x: x["signal_score"], reverse=True)
    return fvgs


def _get_weekly_rsi(ticker: str) -> float:
    """Fetch weekly data and return latest RSI(14) value."""
    try:
        df = _fetch(ticker, "5y", "1wk")
        if df.empty or len(df) < RSI_PERIOD + 1:
            return None
        return float(_rsi_values(df["Close"])[-1])
    except Exception:
        return None


def scan_fvg_investing(ticker: str, weekly_rsi: float = None) -> list:
    """Scan daily/weekly/monthly FVGs for a single ticker."""
    results = []
    for interval, period, tf_label, _ in INVESTING_TFS:
        df = _fetch(ticker, period, interval)
        if df.empty or len(df) < 20:
            continue
        fvgs = _scan_df(df, tf_label, weekly_rsi)
        for f in fvgs:
            f["ticker"]   = ticker
            f["category"] = "investing"
        results.extend(fvgs)
    return results


def scan_fvg_intraday(ticker: str) -> list:
    """Scan 5-min, 10-min (resampled), and 15-min FVGs for a single ticker."""
    results = []

    # 5-min
    df5 = _fetch(ticker, "60d", "5m")
    if not df5.empty and len(df5) >= 20:
        for f in _scan_df(df5, "5min"):
            f["ticker"]   = ticker
            f["category"] = "intraday"
            results.append(f)

    # 10-min (resampled from 5-min)
    if not df5.empty:
        df10 = _resample_10m(df5)
        if not df10.empty and len(df10) >= 20:
            for f in _scan_df(df10, "10min"):
                f["ticker"]   = ticker
                f["category"] = "intraday"
                results.append(f)

    # 15-min
    df15 = _fetch(ticker, "60d", "15m")
    if not df15.empty and len(df15) >= 20:
        for f in _scan_df(df15, "15min"):
            f["ticker"]   = ticker
            f["category"] = "intraday"
            results.append(f)

    return results


def run(tickers: list = None) -> list:
    """
    Full FVG scan across investing + intraday timeframes.
    Saves results to data/fvg_signals.json and returns the list.
    """
    if tickers is None:
        try:
            signals = json.load(open(f"{DATA_DIR}/signals.json"))
            tickers = [s["ticker"] for s in signals[:40]]
        except Exception:
            try:
                from universe import US_STOCKS
                tickers = list(US_STOCKS.keys())[:40]
            except Exception:
                tickers = []

    if not tickers:
        print("[FVG Scanner] No tickers to scan.")
        return []

    print(f"[FVG Scanner] Starting — {len(tickers)} tickers, 6 timeframes...")

    all_signals = []
    for idx, ticker in enumerate(tickers):
        try:
            # Fetch weekly RSI once — shared across investing TF scans
            w_rsi = _get_weekly_rsi(ticker)

            inv_fvgs   = scan_fvg_investing(ticker, w_rsi)
            intra_fvgs = scan_fvg_intraday(ticker)

            # Rule 7: timeframe alignment bonus — daily AND weekly both active
            inv_tfs = {f["timeframe"] for f in inv_fvgs}
            if "daily" in inv_tfs and "weekly" in inv_tfs:
                for f in inv_fvgs:
                    f["signal_score"]   += 1
                    f["multi_tf_align"]  = True

            all_signals.extend(inv_fvgs)
            all_signals.extend(intra_fvgs)

            if (idx + 1) % 10 == 0:
                print(f"[FVG Scanner]  {idx + 1}/{len(tickers)} done...")

        except Exception as e:
            print(f"[FVG Scanner] {ticker}: {e}")

    # Sort: RETEST first, then by score
    status_rank = {"RETEST": 2, "FORMED": 1}
    all_signals.sort(
        key=lambda x: (status_rank.get(x.get("status"), 0), x.get("signal_score", 0)),
        reverse=True,
    )
    all_signals = all_signals[:MAX_SIGNALS]

    json.dump(all_signals, open(FVG_FILE, "w"), indent=2, default=str)

    retest  = sum(1 for s in all_signals if s.get("status") == "RETEST")
    formed  = sum(1 for s in all_signals if s.get("status") == "FORMED")
    inv_n   = sum(1 for s in all_signals if s.get("category") == "investing")
    intra_n = sum(1 for s in all_signals if s.get("category") == "intraday")

    print(
        f"[FVG Scanner] {len(all_signals)} FVGs — "
        f"RETEST:{retest} FORMED:{formed} | "
        f"INVESTING:{inv_n} INTRADAY:{intra_n}"
    )
    return all_signals


if __name__ == "__main__":
    run()
