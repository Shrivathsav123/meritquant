"""
Bond-Dollar-Metals-Crypto Cascade Engine (BDMC)

Watches the rates complex for early moves and outputs probability-weighted
setups in gold, silver, Bitcoin, and rate-sensitive equity sectors before
the move shows up in those charts directly.

STRICT SEPARATION of macro flags:
  - TREASURY_BUYBACK: Treasury cash (TGA) buying back long bonds to suppress
    long-end yields. NOT Fed QE. Reversible. Never mis-label as "QE".
  - FOREIGN_HOLDER_FLOW: TIC data / China holdings selling. Yield-raising +
    dollar-NEGATIVE on selling. Slow-moving, never a scan trigger.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import requests
import pandas as pd

def _rsi_series(close: pd.Series, window: int = 14) -> pd.Series:
    """Pure-Python RSI (Wilder smoothing via simple rolling mean). Safe on all inputs."""
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = (-delta.clip(upper=0)).rolling(window).mean()
    # When loss is 0 (pure uptrend), RSI = 100; avoid dividing by zero
    rs = gain / loss.where(loss != 0, other=float("nan"))
    rsi = 100 - (100 / (1 + rs))
    has_data = gain.notna() & loss.notna()
    rsi = rsi.where(~(has_data & loss.eq(0)), other=100.0)
    return rsi

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIDENCE_THRESHOLD = 60   # Minimum confidence to emit a signal

UNIVERSE = {
    # Rates complex
    "TNX":  {"label": "10Y Yield",       "direction_interpretation": "YIELD_RISE_BEARISH_BONDS"},
    "FVX":  {"label": "5Y Yield",        "direction_interpretation": "YIELD_RISE_BEARISH_BONDS"},
    "TLT":  {"label": "20Y Bond ETF",    "direction_interpretation": "PRICE_RISE_BULLISH"},
    "SHY":  {"label": "2Y Bond ETF",     "direction_interpretation": "PRICE_RISE_BULLISH"},
    # Dollar
    "DX-Y.NYB": {"label": "DXY",         "direction_interpretation": "RISE_DOLLAR_BEARISH_METALS"},
    # Metals
    "GC=F": {"label": "Gold Futures",    "direction_interpretation": "PRICE_RISE_BULLISH"},
    "SI=F": {"label": "Silver Futures",  "direction_interpretation": "PRICE_RISE_BULLISH"},
    "HG=F": {"label": "Copper Futures",  "direction_interpretation": "PRICE_RISE_GROWTH_BULLISH"},
    "GDX":  {"label": "Gold Miners ETF", "direction_interpretation": "PRICE_RISE_BULLISH"},
    # Crypto
    "BTC-USD": {"label": "Bitcoin",      "direction_interpretation": "PRICE_RISE_BULLISH"},
    # Rate-sensitive sectors
    "KRE":  {"label": "Regional Banks",  "direction_interpretation": "PRICE_RISE_BULLISH"},
    "XHB":  {"label": "Homebuilders",    "direction_interpretation": "PRICE_RISE_BULLISH"},
    "XLU":  {"label": "Utilities",       "direction_interpretation": "PRICE_RISE_BULLISH"},
    "SMH":  {"label": "Semis",           "direction_interpretation": "PRICE_RISE_BULLISH"},
    # Equity benchmark for GDX beta check
    "SPY":  {"label": "S&P 500",         "direction_interpretation": "PRICE_RISE_BULLISH"},
    "QQQ":  {"label": "Nasdaq",          "direction_interpretation": "PRICE_RISE_BULLISH"},
    "^VIX": {"label": "VIX",             "direction_interpretation": "RISE_RISK_OFF"},
}

TIMEFRAMES = [
    {"interval": "1h",  "period": "7d",   "label": "1H"},
    {"interval": "2h",  "period": "14d",  "label": "2H"},
    {"interval": "3h",  "period": "14d",  "label": "3H"},
    {"interval": "4h",  "period": "30d",  "label": "4H"},
    {"interval": "1d",  "period": "180d", "label": "1D"},
]

FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv"

TREASURY_BUYBACK_API = (
    "https://fiscaldata.treasury.gov/api/v1/accounting/od/"
    "treasury_securities_buybacks?fields=record_date,cusip,security_type,"
    "face_value_amount&sort=-record_date&page[size]=50"
)

# TIC via FRED proxies
FRED_CHINA_HOLDINGS_SERIES = "BOGZ1FL263061705Q"   # China Treasury holdings
FRED_FOREIGN_HOLDINGS_SERIES = "HQMCB10YR"          # fallback proxy

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RSISignal:
    symbol: str
    timeframe: str
    rsi_value: float
    direction_interpretation: str
    series: str            # "rising" | "falling" | "flat"
    overbought: bool
    oversold: bool


@dataclass
class MacroFlags:
    TREASURY_BUYBACK: bool = False
    treasury_buyback_detail: str = ""   # "Buying back long bonds via TGA. NOT Fed QE. Reversible."
    FOREIGN_HOLDER_FLOW: str = "UNKNOWN"  # "SELLING" | "NEUTRAL" | "BUYING" | "UNKNOWN"
    foreign_holder_detail: str = ""


@dataclass
class BDMCSignal:
    symbol: str
    label: str
    direction: str          # "LONG" | "SHORT" | "NEUTRAL"
    confidence: int         # 0-100
    holding_period: str     # "Scalp" | "Swing" | "Position"
    real_yield_driver: bool
    copper_regime: str      # "REAL_YIELD_SAFE_HAVEN" | "REFLATION" | "RISK_OFF" | "UNKNOWN"
    timeframes_agreeing: list[str]
    rationale: list[str]
    macro_flags: MacroFlags = field(default_factory=MacroFlags)


@dataclass
class BDMCScanResult:
    timestamp: str
    real_yield: Optional[float]
    nominal_10y: Optional[float]
    breakeven_10y: Optional[float]
    spread_2s10s: Optional[float]
    spread_trend: str           # "STEEPENING" | "FLATTENING" | "FLAT"
    dxy_trend: str              # "RISING" | "FALLING" | "FLAT"
    vix_level: Optional[float]
    macro_flags: MacroFlags
    signals: list[BDMCSignal]
    copper_regime: str

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(url: str, timeout: int = 15, params: dict | None = None) -> requests.Response:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/html,application/xhtml+xml,*/*",
    }
    r = requests.get(url, headers=headers, params=params, timeout=timeout)
    r.raise_for_status()
    return r


def fetch_fred_series(series_id: str) -> Optional[float]:
    """Return the most recent value for a FRED series via the public CSV endpoint."""
    try:
        r = _get(FRED_BASE, params={"id": series_id})
        lines = [ln for ln in r.text.strip().splitlines() if ln and not ln.startswith("DATE")]
        if not lines:
            return None
        last = lines[-1].split(",")
        val = last[1].strip()
        return float(val) if val != "." else None
    except Exception as e:
        log.warning("FRED fetch failed for %s: %s", series_id, e)
        return None


def fetch_yahoo_ohlcv(symbol: str, period: str, interval: str) -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV bars via Yahoo Finance v8 chart API.
    Returns DataFrame with columns [open, high, low, close, volume] or None on failure.
    """
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {
            "range": period,
            "interval": interval,
            "includePrePost": "false",
        }
        data = _get(url, params=params).json()
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
        df = pd.DataFrame({
            "close": quote["close"],
            "open":  quote.get("open"),
            "high":  quote.get("high"),
            "low":   quote.get("low"),
            "volume":quote.get("volume"),
        }, index=pd.to_datetime(timestamps, unit="s", utc=True))
        df.dropna(subset=["close"], inplace=True)
        return df
    except Exception as e:
        log.warning("Yahoo fetch failed for %s (%s %s): %s", symbol, period, interval, e)
        return None

# ---------------------------------------------------------------------------
# RSI multi-timeframe scan
# ---------------------------------------------------------------------------

def compute_rsi_signal(symbol: str, df: pd.DataFrame, timeframe_label: str) -> Optional[RSISignal]:
    """
    Compute RSI on a price series. Returns RSISignal with explicit series and
    direction_interpretation fields to prevent sign-flip bugs.
    """
    if df is None or len(df) < 15:
        return None
    try:
        close = df["close"].dropna()
        rsi_series = _rsi_series(close=close, window=14)
        current_rsi = float(rsi_series.iloc[-1])
        prev_rsi = float(rsi_series.iloc[-3])  # 3 bars back for trend

        if current_rsi > prev_rsi + 2:
            series = "rising"
        elif current_rsi < prev_rsi - 2:
            series = "falling"
        else:
            series = "flat"

        meta = UNIVERSE.get(symbol, {})
        direction_interpretation = meta.get("direction_interpretation", "PRICE_RISE_BULLISH")

        return RSISignal(
            symbol=symbol,
            timeframe=timeframe_label,
            rsi_value=round(current_rsi, 1),
            direction_interpretation=direction_interpretation,
            series=series,
            overbought=(current_rsi >= 70),
            oversold=(current_rsi <= 30),
        )
    except Exception as e:
        log.warning("RSI compute error for %s %s: %s", symbol, timeframe_label, e)
        return None


def run_multiframe_rsi(symbol: str) -> list[RSISignal]:
    """
    Run 1H/2H/3H/4H/1D RSI scan for one symbol.
    BTC gets lower confidence weight on 1H-3H due to noisy rate correlation.
    """
    results = []
    for tf in TIMEFRAMES:
        df = fetch_yahoo_ohlcv(symbol, tf["period"], tf["interval"])
        sig = compute_rsi_signal(symbol, df, tf["label"])
        if sig:
            results.append(sig)
    return results

# ---------------------------------------------------------------------------
# RSI percentile (sector extension check)
# ---------------------------------------------------------------------------

def rsi_percentile_6m(symbol: str) -> Optional[float]:
    """
    Compute current RSI as percentile vs its own 6-month daily range.
    Returns 0.0-1.0. Returns None on failure.
    """
    df = fetch_yahoo_ohlcv(symbol, "180d", "1d")
    if df is None or len(df) < 20:
        return None
    try:
        close = df["close"].dropna()
        rsi_series = _rsi_series(close=close, window=14).dropna()
        current = float(rsi_series.iloc[-1])
        lo, hi = float(rsi_series.min()), float(rsi_series.max())
        if hi == lo:
            return 0.5
        return round((current - lo) / (hi - lo), 3)
    except Exception as e:
        log.warning("RSI percentile error %s: %s", symbol, e)
        return None


def sector_is_extended(symbol: str, percentile: Optional[float]) -> bool:
    """True if RSI is in the top decile of its 6-month range (overextended)."""
    return percentile is not None and percentile >= 0.90

# ---------------------------------------------------------------------------
# FRED macro series
# ---------------------------------------------------------------------------

def fetch_real_yield() -> tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Returns (real_yield, nominal_10y, breakeven_10y).
    real_yield = DGS10 - T10YIE (FRED breakeven inflation expectation).
    This is the primary driver for gold/silver, not DXY alone.
    """
    nominal = fetch_fred_series("DGS10")
    breakeven = fetch_fred_series("T10YIE")
    if nominal is not None and breakeven is not None:
        return round(nominal - breakeven, 3), nominal, breakeven
    return None, nominal, breakeven


def fetch_2s10s_spread() -> Optional[float]:
    """2s10s Treasury spread from FRED T10Y2Y series."""
    val = fetch_fred_series("T10Y2Y")
    return val


def fetch_2s10s_trend() -> tuple[Optional[float], str]:
    """
    Returns (current_spread, trend).
    Trend is STEEPENING / FLATTENING / FLAT based on 10-day change.
    """
    try:
        r = _get(FRED_BASE, params={"id": "T10Y2Y"})
        lines = [ln for ln in r.text.strip().splitlines() if ln and not ln.startswith("DATE")]
        if len(lines) < 12:
            return None, "FLAT"
        recent = float(lines[-1].split(",")[1]) if lines[-1].split(",")[1] != "." else None
        older = float(lines[-12].split(",")[1]) if lines[-12].split(",")[1] != "." else None
        if recent is None or older is None:
            return recent, "FLAT"
        delta = recent - older
        if delta > 0.05:
            trend = "STEEPENING"
        elif delta < -0.05:
            trend = "FLATTENING"
        else:
            trend = "FLAT"
        return round(recent, 3), trend
    except Exception as e:
        log.warning("2s10s trend error: %s", e)
        return None, "FLAT"

# ---------------------------------------------------------------------------
# Treasury Buyback flag (STRICTLY separate from Fed QE)
# ---------------------------------------------------------------------------

def fetch_treasury_buyback_flag() -> MacroFlags:
    """
    Check fiscaldata.treasury.gov for recent Treasury buyback activity.
    TREASURY_BUYBACK = True means Treasury is using TGA cash to repurchase
    outstanding long-dated bonds, suppressing long-end yields.
    THIS IS NOT FED QE. It is a discretionary Treasury operation and is reversible.
    Never label this as quantitative easing in reports.
    """
    flags = MacroFlags()
    try:
        r = _get(TREASURY_BUYBACK_API, timeout=20)
        data = r.json()
        entries = data.get("data", [])
        if not entries:
            return flags
        # Check if there are buyback operations in the last 30 days
        cutoff = (datetime.now(timezone.utc).timestamp() - 30 * 86400)
        recent = []
        for e in entries:
            try:
                dt = datetime.strptime(e["record_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if dt.timestamp() >= cutoff:
                    recent.append(e)
            except Exception:
                continue

        if recent:
            total_face = sum(float(e.get("face_value_amount", 0) or 0) for e in recent)
            flags.TREASURY_BUYBACK = True
            flags.treasury_buyback_detail = (
                f"Treasury buyback active: {len(recent)} operations last 30d, "
                f"~${total_face/1e9:.1f}B face value. "
                "Buying back long bonds via TGA cash. NOT Fed QE. Reversible. "
                "Suppresses long-end yields, supportive of gold/silver/duration assets."
            )
    except Exception as e:
        log.warning("Treasury buyback fetch failed: %s", e)
    return flags


# ---------------------------------------------------------------------------
# Foreign Holder Flow flag (TIC data, slow-moving, never a scan trigger)
# ---------------------------------------------------------------------------

def fetch_foreign_holder_flow() -> str:
    """
    Returns "SELLING" | "BUYING" | "NEUTRAL" | "UNKNOWN" based on FRED
    proxy for China/foreign Treasury holdings. Data has ~6-week release lag.
    This flag is informational only — it never triggers a BDMC scan signal.
    When foreign holders sell, yields rise AND the dollar weakens (dollar-negative
    because they repatriate proceeds). This is the opposite of domestic risk-off.
    """
    try:
        r = _get(FRED_BASE, params={"id": FRED_CHINA_HOLDINGS_SERIES})
        lines = [ln for ln in r.text.strip().splitlines() if ln and not ln.startswith("DATE")]
        if len(lines) < 3:
            return "UNKNOWN"
        vals = []
        for ln in lines[-4:]:
            parts = ln.split(",")
            if len(parts) >= 2 and parts[1] != ".":
                vals.append(float(parts[1]))
        if len(vals) < 2:
            return "UNKNOWN"
        delta = vals[-1] - vals[-2]
        if delta < -5:      # $5B+ decline
            return "SELLING"
        elif delta > 5:
            return "BUYING"
        return "NEUTRAL"
    except Exception as e:
        log.warning("Foreign holder flow fetch failed: %s", e)
        return "UNKNOWN"

# ---------------------------------------------------------------------------
# DXY trend
# ---------------------------------------------------------------------------

def fetch_dxy_trend() -> tuple[Optional[float], str]:
    """Returns (current_dxy, trend: RISING|FALLING|FLAT) from daily 30d series."""
    df = fetch_yahoo_ohlcv("DX-Y.NYB", "30d", "1d")
    if df is None or len(df) < 5:
        return None, "FLAT"
    closes = df["close"].dropna()
    current = float(closes.iloc[-1])
    prior = float(closes.iloc[-10]) if len(closes) >= 10 else float(closes.iloc[0])
    pct = (current - prior) / prior * 100
    if pct > 0.5:
        trend = "RISING"
    elif pct < -0.5:
        trend = "FALLING"
    else:
        trend = "FLAT"
    return round(current, 3), trend

# ---------------------------------------------------------------------------
# VIX
# ---------------------------------------------------------------------------

def fetch_vix() -> Optional[float]:
    df = fetch_yahoo_ohlcv("^VIX", "5d", "1d")
    if df is None or df.empty:
        return None
    return round(float(df["close"].dropna().iloc[-1]), 2)

# ---------------------------------------------------------------------------
# GDX equity-beta penalty
# ---------------------------------------------------------------------------

def gdx_equity_beta_penalty(vix: Optional[float]) -> tuple[int, str]:
    """
    Gold miners (GDX) carry ~2-3x operating leverage on gold but also carry
    SPY/QQQ beta. Check equity trend and VIX level.
    Returns (penalty_points, rationale). Penalty is subtracted from confidence.
    """
    penalty = 0
    rationale_parts = []

    spy_df = fetch_yahoo_ohlcv("SPY", "30d", "1d")
    qqq_df = fetch_yahoo_ohlcv("QQQ", "30d", "1d")

    spy_declining = False
    qqq_declining = False

    if spy_df is not None and len(spy_df) >= 10:
        s = spy_df["close"].dropna()
        if float(s.iloc[-1]) < float(s.iloc[-10]):
            spy_declining = True

    if qqq_df is not None and len(qqq_df) >= 10:
        q = qqq_df["close"].dropna()
        if float(q.iloc[-1]) < float(q.iloc[-10]):
            qqq_declining = True

    if spy_declining and qqq_declining:
        penalty += 15
        rationale_parts.append("SPY+QQQ both declining (-15)")
    elif spy_declining or qqq_declining:
        penalty += 8
        rationale_parts.append("Equity index declining (-8)")

    if vix is not None:
        if vix >= 30:
            penalty += 10
            rationale_parts.append(f"VIX={vix:.1f} elevated (-10)")
        elif vix >= 22:
            penalty += 5
            rationale_parts.append(f"VIX={vix:.1f} cautious (-5)")

    if not rationale_parts:
        rationale_parts.append("Equity beta check OK")

    return penalty, "; ".join(rationale_parts)

# ---------------------------------------------------------------------------
# Copper regime
# ---------------------------------------------------------------------------

def classify_copper_regime(gold_rsi_sigs: list[RSISignal], copper_rsi_sigs: list[RSISignal]) -> str:
    """
    Copper is a separate growth/China demand driver.
    - Copper falling + Gold rising → real-yield / safe-haven story
    - Both rising → reflation regime
    - Both falling → risk-off
    """
    def net_direction(sigs: list[RSISignal]) -> str:
        if not sigs:
            return "flat"
        rising = sum(1 for s in sigs if s.series == "rising")
        falling = sum(1 for s in sigs if s.series == "falling")
        if rising > falling:
            return "rising"
        if falling > rising:
            return "falling"
        return "flat"

    gold_dir = net_direction(gold_rsi_sigs)
    copper_dir = net_direction(copper_rsi_sigs)

    if copper_dir == "falling" and gold_dir == "rising":
        return "REAL_YIELD_SAFE_HAVEN"
    if copper_dir == "rising" and gold_dir == "rising":
        return "REFLATION"
    if copper_dir == "falling" and gold_dir == "falling":
        return "RISK_OFF"
    return "UNKNOWN"

# ---------------------------------------------------------------------------
# Holding period classifier
# ---------------------------------------------------------------------------

def classify_holding_period(agreeing_timeframes: list[str], macro_flags: MacroFlags) -> str:
    """
    Scalp:    only 1H, 2H, 3H agree
    Swing:    4H and/or 1D agree
    Position: 1D agrees + structural flags (buyback, foreign flow, real yield extreme)
    """
    has_daily = "1D" in agreeing_timeframes
    has_4h = "4H" in agreeing_timeframes
    structural = (
        macro_flags.TREASURY_BUYBACK
        or macro_flags.FOREIGN_HOLDER_FLOW == "SELLING"
    )

    if has_daily and structural:
        return "Position"
    if has_daily or has_4h:
        return "Swing"
    return "Scalp"

# ---------------------------------------------------------------------------
# Confidence scorer
# ---------------------------------------------------------------------------

def score_confidence(
    rsi_signals: list[RSISignal],
    target_direction: str,       # "bullish" | "bearish"
    real_yield: Optional[float],
    dxy_trend: str,
    macro_flags: MacroFlags,
    symbol: str,
    gdx_penalty: int = 0,
    spread_trend: str = "FLAT",
) -> tuple[int, list[str], list[str]]:
    """
    Score 0-100. Returns (score, agreeing_timeframes, rationale_list).

    Factors:
    1. Timeframe agreement (up to 50 pts)
    2. Real yield confirmation (15 pts)
    3. DXY confirmation (10 pts)
    4. Treasury buyback alignment (10 pts)
    5. GDX equity-beta penalty (subtracted)
    6. Curve shape for KRE (gated on spread_trend)
    """
    score = 0
    rationale = []
    agreeing = []

    # ── 1. Timeframe agreement ──────────────────────────────────────────────
    for sig in rsi_signals:
        interp = sig.direction_interpretation
        # Determine if this timeframe agrees with target direction
        tf_bullish = (
            (interp in ("PRICE_RISE_BULLISH", "PRICE_RISE_GROWTH_BULLISH") and sig.series == "rising")
            or (interp == "YIELD_RISE_BEARISH_BONDS" and sig.series == "falling")   # yields falling = bonds bullish
            or (interp == "RISE_DOLLAR_BEARISH_METALS" and sig.series == "falling") # DXY falling = metals bullish
        )
        tf_bearish = (
            (interp in ("PRICE_RISE_BULLISH", "PRICE_RISE_GROWTH_BULLISH") and sig.series == "falling")
            or (interp == "YIELD_RISE_BEARISH_BONDS" and sig.series == "rising")
            or (interp == "RISE_DOLLAR_BEARISH_METALS" and sig.series == "rising")
        )

        agrees = (target_direction == "bullish" and tf_bullish) or \
                 (target_direction == "bearish" and tf_bearish)

        if agrees:
            agreeing.append(sig.timeframe)
            pts = {"1H": 5, "2H": 7, "3H": 8, "4H": 12, "1D": 18}.get(sig.timeframe, 5)
            # BTC gets reduced weight on short timeframes (noisy rate correlation)
            if symbol == "BTC-USD" and sig.timeframe in ("1H", "2H", "3H"):
                pts = max(1, pts // 2)
            score += pts

    if agreeing:
        rationale.append(f"Timeframes agreeing: {', '.join(agreeing)}")
    else:
        rationale.append("No timeframe agreement")

    score = min(score, 50)

    # ── 2. Real yield confirmation ──────────────────────────────────────────
    # Falling real yield → bullish gold/silver/BTC
    # Rising real yield → bearish gold/silver/BTC
    metals = ("GC=F", "SI=F", "GDX", "BTC-USD")
    if symbol in metals and real_yield is not None:
        if target_direction == "bullish" and real_yield < 0.5:
            score += 15
            rationale.append(f"Real yield={real_yield:.2f}% supports gold/metals")
        elif target_direction == "bullish" and real_yield >= 1.5:
            score -= 10
            rationale.append(f"Real yield={real_yield:.2f}% headwind for metals")
        elif target_direction == "bearish" and real_yield >= 1.5:
            score += 15
            rationale.append(f"Real yield={real_yield:.2f}% supports metals short")

    # ── 3. DXY confirmation ─────────────────────────────────────────────────
    if symbol in metals:
        if target_direction == "bullish" and dxy_trend == "FALLING":
            score += 10
            rationale.append("DXY falling: metals tailwind")
        elif target_direction == "bullish" and dxy_trend == "RISING":
            score -= 5
            rationale.append("DXY rising: metals headwind")
        elif target_direction == "bearish" and dxy_trend == "RISING":
            score += 10
            rationale.append("DXY rising: confirms metals short")

    # ── 4. Treasury buyback ─────────────────────────────────────────────────
    if macro_flags.TREASURY_BUYBACK:
        if symbol in metals and target_direction == "bullish":
            score += 10
            rationale.append("Treasury buyback suppressing long-end yields: gold/silver supportive")
        elif symbol == "KRE" and target_direction == "bullish":
            score += 5
            rationale.append("Treasury buyback supportive of curve steepening: mild KRE benefit")

    # ── 5. KRE 2s10s gating ─────────────────────────────────────────────────
    # KRE (regional banks) NIM is sensitive to curve SHAPE.
    # Bull-steepener → supportive. Bull-flattener → NOT supportive.
    # KRE and XHB have DIFFERENT sensitivity; do not use same sign.
    if symbol == "KRE":
        if target_direction == "bullish" and spread_trend == "STEEPENING":
            score += 12
            rationale.append("2s10s STEEPENING: KRE NIM improving")
        elif target_direction == "bullish" and spread_trend == "FLATTENING":
            score -= 15
            rationale.append("2s10s FLATTENING: KRE NIM compressed, reduce confidence")
        elif target_direction == "bullish" and spread_trend == "FLAT":
            pass  # neutral

    # ── 6. GDX equity-beta penalty ──────────────────────────────────────────
    if symbol == "GDX" and gdx_penalty > 0:
        score -= gdx_penalty
        rationale.append(f"GDX equity-beta penalty applied: -{gdx_penalty}")

    score = max(0, min(100, score))
    return score, agreeing, rationale

# ---------------------------------------------------------------------------
# Per-symbol signal builder
# ---------------------------------------------------------------------------

def build_signal(
    symbol: str,
    rsi_signals: list[RSISignal],
    real_yield: Optional[float],
    dxy_trend: str,
    macro_flags: MacroFlags,
    gdx_penalty: int,
    spread_trend: str,
    vix: Optional[float],
    copper_regime: str,
) -> Optional[BDMCSignal]:
    """
    Build a BDMCSignal for one symbol by determining dominant direction and
    scoring it. Returns None if no signal passes the confidence threshold.
    """
    meta = UNIVERSE.get(symbol, {})
    label = meta.get("label", symbol)
    interp = meta.get("direction_interpretation", "PRICE_RISE_BULLISH")

    # Determine net direction from RSI signals
    rising_count = sum(1 for s in rsi_signals if s.series == "rising")
    falling_count = sum(1 for s in rsi_signals if s.series == "falling")
    total = len(rsi_signals)

    if total == 0:
        return None

    # Map instrument direction interpretation to bullish/bearish signal direction
    if rising_count > falling_count:
        raw_direction = "rising"
    elif falling_count > rising_count:
        raw_direction = "falling"
    else:
        return None  # no dominant direction

    # Translate raw_direction → market signal direction based on instrument semantics
    if interp == "PRICE_RISE_BULLISH":
        target_dir = "bullish" if raw_direction == "rising" else "bearish"
    elif interp == "PRICE_RISE_GROWTH_BULLISH":
        target_dir = "bullish" if raw_direction == "rising" else "bearish"
    elif interp == "YIELD_RISE_BEARISH_BONDS":
        # For yields: rising yield RSI → bonds bearish. Falling yield RSI → bonds bullish.
        target_dir = "bearish" if raw_direction == "rising" else "bullish"
    elif interp == "RISE_DOLLAR_BEARISH_METALS":
        # For DXY: rising DXY RSI → metals bearish. Falling DXY RSI → metals bullish.
        target_dir = "bearish" if raw_direction == "rising" else "bullish"
    elif interp == "RISE_RISK_OFF":
        target_dir = "bearish" if raw_direction == "rising" else "bullish"
    else:
        target_dir = "bullish" if raw_direction == "rising" else "bearish"

    score, agreeing, rationale = score_confidence(
        rsi_signals=rsi_signals,
        target_direction=target_dir,
        real_yield=real_yield,
        dxy_trend=dxy_trend,
        macro_flags=macro_flags,
        symbol=symbol,
        gdx_penalty=gdx_penalty,
        spread_trend=spread_trend,
    )

    if score < CONFIDENCE_THRESHOLD:
        return None

    holding_period = classify_holding_period(agreeing, macro_flags)

    # Sector extension check — suppress if RSI is in top decile
    sectors = ("SMH", "XHB", "KRE", "XLU")
    if symbol in sectors:
        pct = rsi_percentile_6m(symbol)
        if sector_is_extended(symbol, pct):
            rationale.append(
                f"Signal suppressed: {label} RSI at {pct:.0%} of 6-month range (top decile, overextended)"
            )
            return None
        if pct is not None:
            rationale.append(f"{label} RSI at {pct:.0%} of 6-month range")

    direction = "LONG" if target_dir == "bullish" else "SHORT"

    real_yield_driver = (symbol in ("GC=F", "SI=F", "GDX") and real_yield is not None)

    return BDMCSignal(
        symbol=symbol,
        label=label,
        direction=direction,
        confidence=score,
        holding_period=holding_period,
        real_yield_driver=real_yield_driver,
        copper_regime=copper_regime,
        timeframes_agreeing=agreeing,
        rationale=rationale,
        macro_flags=macro_flags,
    )

# ---------------------------------------------------------------------------
# Main scan entry point
# ---------------------------------------------------------------------------

def run_bdmc_scan() -> BDMCScanResult:
    """
    Run the full BDMC cascade scan. Called from the main scan loop.
    Returns a BDMCScanResult with all signals that pass the 60/100 threshold.
    Does NOT auto-execute any trades.
    """
    log.info("BDMC: starting cascade scan")
    ts = datetime.now(timezone.utc).isoformat()

    # ── Macro context ────────────────────────────────────────────────────────
    real_yield, nominal_10y, breakeven = fetch_real_yield()
    spread, spread_trend = fetch_2s10s_trend()
    dxy_val, dxy_trend = fetch_dxy_trend()
    vix = fetch_vix()

    # ── Macro flags (strictly separate) ─────────────────────────────────────
    macro_flags = fetch_treasury_buyback_flag()
    macro_flags.FOREIGN_HOLDER_FLOW = fetch_foreign_holder_flow()

    # ── GDX equity-beta penalty (computed once, applied to GDX only) ─────────
    gdx_penalty, gdx_rationale = gdx_equity_beta_penalty(vix)
    log.info("BDMC: GDX equity-beta penalty=%d (%s)", gdx_penalty, gdx_rationale)

    # ── Multi-timeframe RSI for all instruments ──────────────────────────────
    rsi_map: dict[str, list[RSISignal]] = {}
    for symbol in UNIVERSE:
        if symbol in ("^VIX",):
            continue    # VIX used for penalty only, not RSI signal target
        log.info("BDMC: scanning %s", symbol)
        rsi_map[symbol] = run_multiframe_rsi(symbol)
        time.sleep(0.3)  # rate limit

    # ── Copper regime ────────────────────────────────────────────────────────
    copper_regime = classify_copper_regime(
        gold_rsi_sigs=rsi_map.get("GC=F", []),
        copper_rsi_sigs=rsi_map.get("HG=F", []),
    )

    # ── Build signals ────────────────────────────────────────────────────────
    signals: list[BDMCSignal] = []
    for symbol, rsi_sigs in rsi_map.items():
        if symbol in ("SPY", "QQQ"):
            continue  # equity benchmarks used for penalty only
        sig = build_signal(
            symbol=symbol,
            rsi_signals=rsi_sigs,
            real_yield=real_yield,
            dxy_trend=dxy_trend,
            macro_flags=macro_flags,
            gdx_penalty=gdx_penalty if symbol == "GDX" else 0,
            spread_trend=spread_trend,
            vix=vix,
            copper_regime=copper_regime,
        )
        if sig is not None:
            signals.append(sig)

    signals.sort(key=lambda s: s.confidence, reverse=True)

    return BDMCScanResult(
        timestamp=ts,
        real_yield=real_yield,
        nominal_10y=nominal_10y,
        breakeven_10y=breakeven,
        spread_2s10s=spread,
        spread_trend=spread_trend,
        dxy_trend=dxy_trend,
        vix_level=vix,
        macro_flags=macro_flags,
        signals=signals,
        copper_regime=copper_regime,
    )

# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------

def format_bdmc_report_section(result: BDMCScanResult) -> dict:
    """
    Returns a structured dict consumed by:
    - trader.py build_pdf()    → "Bond-Dollar-Cascade Read" section
    - trader.py build_excel()  → BDMC sheet
    - trader.py session_msg()  → Telegram BDMC block

    No formatting logic lives in this function — only data.
    """
    signal_rows = []
    for sig in result.signals:
        signal_rows.append({
            "symbol": sig.symbol,
            "label": sig.label,
            "direction": sig.direction,
            "confidence": sig.confidence,
            "holding_period": sig.holding_period,
            "timeframes": ", ".join(sig.timeframes_agreeing),
            "real_yield_driver": sig.real_yield_driver,
            "copper_regime": sig.copper_regime,
            "rationale": " | ".join(sig.rationale),
        })

    buyback_flag = result.macro_flags.TREASURY_BUYBACK
    buyback_detail = result.macro_flags.treasury_buyback_detail or "No active Treasury buyback operations detected."
    foreign_flow = result.macro_flags.FOREIGN_HOLDER_FLOW
    foreign_detail = result.macro_flags.foreign_holder_detail or (
        "Foreign holder flow (TIC data, ~6-week lag): "
        + {"SELLING": "Net selling of UST — yield-raising, dollar-NEGATIVE (NOT same as domestic risk-off).",
           "BUYING": "Net buying of UST — yield-suppressing, dollar-supportive.",
           "NEUTRAL": "No significant TIC flow detected.",
           "UNKNOWN": "TIC data unavailable."}.get(foreign_flow, "Unknown.")
    )

    return {
        "section": "Bond-Dollar-Cascade Read",
        "timestamp": result.timestamp,
        "macro_context": {
            "real_yield": result.real_yield,
            "nominal_10y": result.nominal_10y,
            "breakeven_10y": result.breakeven_10y,
            "spread_2s10s": result.spread_2s10s,
            "spread_trend": result.spread_trend,
            "dxy_trend": result.dxy_trend,
            "vix": result.vix_level,
            "copper_regime": result.copper_regime,
        },
        "macro_flags": {
            "TREASURY_BUYBACK": buyback_flag,
            "treasury_buyback_detail": buyback_detail,
            "FOREIGN_HOLDER_FLOW": foreign_flow,
            "foreign_holder_detail": foreign_detail,
        },
        "signals": signal_rows,
        "signal_count": len(signal_rows),
        "confidence_threshold": CONFIDENCE_THRESHOLD,
    }


def format_bdmc_telegram(result: BDMCScanResult) -> str:
    """
    Returns a Telegram-ready text block for session_msg().
    """
    lines = [
        "📐 *Bond-Dollar-Cascade Read*",
        f"Real Yield: {result.real_yield:.2f}%" if result.real_yield is not None else "Real Yield: N/A",
        f"2s10s Spread: {result.spread_2s10s:.2f}bp ({result.spread_trend})" if result.spread_2s10s is not None else f"2s10s: N/A ({result.spread_trend})",
        f"DXY: {result.dxy_trend}  |  VIX: {result.vix_level:.1f}" if result.vix_level else f"DXY: {result.dxy_trend}",
        f"Copper Regime: {result.copper_regime}",
        "",
    ]

    if result.macro_flags.TREASURY_BUYBACK:
        lines.append("⚠️ TREASURY_BUYBACK ACTIVE (NOT QE — reversible TGA operation)")
    if result.macro_flags.FOREIGN_HOLDER_FLOW == "SELLING":
        lines.append("⚠️ FOREIGN_HOLDER_FLOW: SELLING (yield↑, dollar-NEGATIVE — TIC lag ~6wk)")

    if not result.signals:
        lines.append("No cascade signals above 60/100 threshold.")
    else:
        lines.append(f"*{len(result.signals)} cascade signal(s):*")
        for sig in result.signals:
            arrow = "↑" if sig.direction == "LONG" else "↓"
            lines.append(
                f"  {arrow} {sig.label} ({sig.symbol}) | {sig.direction} | "
                f"Conf: {sig.confidence}/100 | {sig.holding_period} | "
                f"TF: {', '.join(sig.timeframes_agreeing)}"
            )

    return "\n".join(lines)
