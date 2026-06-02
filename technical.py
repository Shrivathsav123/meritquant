# technical.py — Your exact trading rules coded into an engine
import yfinance as yf
import pandas as pd
import numpy as np
import ta
from datetime import datetime

# ── Your RSI Rules ────────────────────────────────────────────
RSI_OVERSOLD_STRONG  = 35   # Strong buy zone
RSI_OVERSOLD_MEDIUM  = 45   # Medium buy zone
RSI_OVERSOLD_MAX     = 50   # Max entry if very bullish
RSI_OVERBOUGHT       = 70   # Sell zone (but hold if momentum strong)

# ── Timeframe map ─────────────────────────────────────────────
TIMEFRAMES = {
    "3h":      {"interval": "1h",  "period": "5d",   "label": "3H"},
    "4h":      {"interval": "1h",  "period": "5d",   "label": "4H"},
    "daily":   {"interval": "1d",  "period": "6mo",  "label": "Daily"},
    "weekly":  {"interval": "1wk", "period": "2y",   "label": "Weekly"},
    "monthly": {"interval": "1mo", "period": "5y",   "label": "Monthly"},
}

def get_rsi(series, period=14):
    """Calculate RSI."""
    try:
        return ta.momentum.RSIIndicator(series, window=period).rsi()
    except:
        return pd.Series([50] * len(series))

def get_macd(series):
    """Calculate MACD."""
    try:
        macd = ta.trend.MACD(series)
        return macd.macd(), macd.macd_signal(), macd.macd_diff()
    except:
        return pd.Series([0]*len(series)), pd.Series([0]*len(series)), pd.Series([0]*len(series))

def get_ad(high, low, close, volume):
    """Accumulation/Distribution line."""
    try:
        return ta.volume.AccDistIndexIndicator(high, low, close, volume).acc_dist_index()
    except:
        return pd.Series([0] * len(close))

def get_rsi_score(ticker):
    """
    Score RSI across multiple timeframes.
    Returns score (0-3) and details dict.
    Your rule: more timeframes oversold = stronger signal
    """
    score = 0
    details = {}
    oversold_count = 0

    timeframe_checks = [
        ("3h",    "1h",  "5d"),
        ("4h",    "1h",  "5d"),
        ("daily", "1d",  "3mo"),
        ("weekly","1wk", "1y"),
    ]

    for tf_name, interval, period in timeframe_checks:
        try:
            data = yf.download(ticker, interval=interval, period=period,
                             progress=False, auto_adjust=True)
            if data.empty or len(data) < 15:
                continue

            rsi_series = get_rsi(data["Close"])
            current_rsi = float(rsi_series.iloc[-1])
            prev_rsi    = float(rsi_series.iloc[-2]) if len(rsi_series) > 1 else current_rsi

            details[tf_name] = {
                "rsi": round(current_rsi, 1),
                "direction": "↑" if current_rsi > prev_rsi else "↓",
                "oversold": current_rsi <= RSI_OVERSOLD_MAX,
                "strong_oversold": current_rsi <= RSI_OVERSOLD_STRONG,
            }

            if current_rsi <= RSI_OVERSOLD_STRONG:
                oversold_count += 1
            elif current_rsi <= RSI_OVERSOLD_MAX:
                oversold_count += 0.5

        except Exception as e:
            details[tf_name] = {"rsi": None, "error": str(e)}

    # Your scoring: more TFs oversold = stronger
    if oversold_count >= 3:
        score = 3   # 🔥 3+ timeframes = strongest swing signal
    elif oversold_count >= 2:
        score = 2   # ⚡ 2 timeframes = strong
    elif oversold_count >= 1:
        score = 1   # 👀 1 timeframe = watch

    return score, details, oversold_count

def get_ma_score(ticker):
    """
    Score moving averages.
    Your rules:
    - Above 200MA = bullish (support)
    - Below 200MA + catalyst = short trade (resistance)
    - Golden Cross (50MA > 200MA) = +2
    - Death Cross (50MA < 200MA) = bearish flag
    """
    score = 0
    details = {}

    try:
        data = yf.download(ticker, interval="1d", period="1y",
                          progress=False, auto_adjust=True)
        if data.empty or len(data) < 50:
            return 0, {}

        close = data["Close"]
        current_price = float(close.iloc[-1])

        ma50  = float(close.rolling(50).mean().iloc[-1])
        ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None

        # Previous values for cross detection
        ma50_prev  = float(close.rolling(50).mean().iloc[-2])
        ma200_prev = float(close.rolling(200).mean().iloc[-2]) if len(close) >= 200 else None

        details["price"]  = round(current_price, 2)
        details["ma50"]   = round(ma50, 2)
        details["ma200"]  = round(ma200, 2) if ma200 else None

        if ma200:
            above_200 = current_price > ma200
            details["above_200ma"] = above_200

            if above_200:
                score += 1  # Bullish bias
                details["ma_signal"] = "✅ Above 200MA (support)"
            else:
                details["ma_signal"] = "⚠️ Below 200MA (resistance for short trade)"

            # Golden Cross detection
            if ma50_prev and ma200_prev:
                golden_cross = ma50 > ma200 and ma50_prev <= ma200_prev
                death_cross  = ma50 < ma200 and ma50_prev >= ma200_prev
                active_golden = ma50 > ma200

                if golden_cross:
                    score += 2
                    details["cross"] = "🔥 GOLDEN CROSS just formed!"
                elif active_golden:
                    score += 1
                    details["cross"] = "✅ Golden Cross active"
                elif death_cross:
                    details["cross"] = "🔴 DEATH CROSS just formed"
                else:
                    details["cross"] = "⚠️ Below 50MA"

        # Price near MA (within 2%) = strong support/resistance level
        if ma200 and abs(current_price - ma200) / ma200 < 0.02:
            score += 1
            details["near_200ma"] = "📍 Price at 200MA — key level"

    except Exception as e:
        details["error"] = str(e)

    return score, details

def get_ad_score(ticker):
    """
    Score Accumulation/Distribution.
    Your rule: Rising A/D = institutions loading = +1 bonus
    """
    score = 0
    details = {}

    try:
        data = yf.download(ticker, interval="1d", period="3mo",
                          progress=False, auto_adjust=True)
        if data.empty or len(data) < 20:
            return 0, {}

        ad_line = get_ad(data["High"], data["Low"], data["Close"], data["Volume"])

        # Check if A/D is rising over last 5 days
        recent_ad = ad_line.tail(5)
        ad_rising = recent_ad.iloc[-1] > recent_ad.iloc[0]
        ad_slope  = float(recent_ad.iloc[-1] - recent_ad.iloc[0])

        details["ad_rising"]   = ad_rising
        details["ad_strength"] = "Strong" if abs(ad_slope) > recent_ad.mean() * 0.01 else "Weak"

        if ad_rising:
            score = 1
            details["signal"] = "✅ Institutions accumulating (A/D rising)"
        else:
            details["signal"] = "⚠️ A/D flat/declining — no institutional buying"

    except Exception as e:
        details["error"] = str(e)

    return score, details


def get_fibonacci_levels(swing_high, swing_low):
    """
    Calculate Fibonacci retracement levels.
    Your rule: buy when price pulls back to 38.2%, 50%, or 61.8%
    61.8% = golden ratio = strongest entry zone
    """
    diff = swing_high - swing_low
    return {
        "0.0%":   round(float(swing_high), 2),
        "23.6%":  round(float(swing_high - 0.236 * diff), 2),
        "38.2%":  round(float(swing_high - 0.382 * diff), 2),
        "50.0%":  round(float(swing_high - 0.500 * diff), 2),
        "61.8%":  round(float(swing_high - 0.618 * diff), 2),
        "78.6%":  round(float(swing_high - 0.786 * diff), 2),
        "100%":   round(float(swing_low), 2),
    }

def detect_patterns(ticker):
    """
    Detect your chart patterns:
    - Fair Value Gap (FVG)
    - Support & Resistance levels
    - Breakout
    - Supply & Demand zones
    - BOS / CHoCH (structure)
    - Divergence (RSI vs price)
    - Volume spike
    """
    patterns = []
    details = {}

    try:
        data = yf.download(ticker, interval="1d", period="6mo",
                          progress=False, auto_adjust=True)
        if data.empty or len(data) < 20:
            return patterns, details

        close  = data["Close"].values
        high   = data["High"].values
        low    = data["Low"].values
        volume = data["Volume"].values
        current = close[-1]

        # ── Fair Value Gap ────────────────────────────────────
        # FVG: gap between candle[i-2] high and candle[i] low
        for i in range(2, len(close)):
            gap_up   = low[i] > high[i-2]    # Bullish FVG
            gap_down = high[i] < low[i-2]    # Bearish FVG
            if gap_up and i >= len(close) - 5:
                patterns.append("📊 Bullish FVG (buy on pullback)")
                details["fvg"] = {"type": "bullish", "level": round(float(high[i-2]), 2)}
            if gap_down and i >= len(close) - 5:
                patterns.append("📊 Bearish FVG")

        # ── Support & Resistance ──────────────────────────────
        # Simple: recent swing highs/lows
        recent_high = max(high[-20:])
        recent_low  = min(low[-20:])

        if abs(current - recent_low) / current < 0.03:
            patterns.append(f"📍 Near Support: ${recent_low:.2f}")
            details["support"] = round(float(recent_low), 2)

        if abs(current - recent_high) / current < 0.02:
            patterns.append(f"🚀 Testing Resistance: ${recent_high:.2f}")
            details["resistance"] = round(float(recent_high), 2)

        # ── Breakout ──────────────────────────────────────────
        # Price breaking above 20-day high with volume
        if current > max(high[-21:-1]) and volume[-1] > volume[-20:].mean() * 1.5:
            patterns.append("🚀 BREAKOUT — Above 20D high with volume!")
            details["breakout"] = True

        # ── Volume Spike ──────────────────────────────────────
        avg_vol = volume[-20:].mean()
        if volume[-1] > avg_vol * 2:
            patterns.append(f"🔊 Volume Spike: {volume[-1]/avg_vol:.1f}x average")
            details["volume_spike"] = round(float(volume[-1]/avg_vol), 1)


        # ── Fibonacci Retracements ────────────────────────────
        # Find the swing high and low over last 50 days
        if len(high) >= 50:
            swing_high = max(high[-50:])
            swing_low  = min(low[-50:])
            fib_levels = get_fibonacci_levels(swing_high, swing_low)

            # Check if current price is near a key Fib level (within 1.5%)
            key_fibs = {
                "38.2%": fib_levels["38.2%"],
                "50.0%": fib_levels["50.0%"],
                "61.8%": fib_levels["61.8%"],  # Golden ratio — strongest
            }

            for level_name, level_price in key_fibs.items():
                distance = abs(current - level_price) / current
                if distance < 0.015:  # Within 1.5% of fib level
                    emoji = "🌟" if level_name == "61.8%" else "📐"
                    strength = "GOLDEN RATIO" if level_name == "61.8%" else "key level"
                    patterns.append(f"{emoji} Fib {level_name} {strength}: ${level_price:.2f}")
                    details[f"fib_{level_name}"] = level_price
                    break  # Only flag closest level

            details["fib_levels"] = fib_levels

        # ── RSI Divergence ────────────────────────────────────
        rsi = get_rsi(pd.Series(close)).values
        # Bullish divergence: price makes lower low but RSI makes higher low
        if (close[-1] < close[-5] and rsi[-1] > rsi[-5] and rsi[-1] < 50):
            patterns.append("⚡ Bullish RSI Divergence")
            details["divergence"] = "bullish"

        # Bearish divergence: price makes higher high but RSI makes lower high
        if (close[-1] > close[-5] and rsi[-1] < rsi[-5] and rsi[-1] > 60):
            patterns.append("⚠️ Bearish RSI Divergence")
            details["divergence"] = "bearish"

        # ── Market Structure (BOS/CHoCH) ──────────────────────
        # Higher highs and higher lows = uptrend
        hh = high[-1] > high[-5] and high[-5] > high[-10]
        hl = low[-1] > low[-5] and low[-5] > low[-10]
        lh = high[-1] < high[-5]
        ll = low[-1] < low[-5]

        if hh and hl:
            patterns.append("📈 Market Structure: Higher Highs & Higher Lows (Uptrend)")
            details["structure"] = "uptrend"
        elif lh and ll:
            patterns.append("📉 Market Structure: Lower Highs & Lower Lows (Downtrend)")
            details["structure"] = "downtrend"

        # ── Supply & Demand ───────────────────────────────────
        # Big move up from a base = demand zone below
        if close[-1] > close[-10] * 1.05 and volume[-1] > avg_vol:
            patterns.append(f"🟢 Demand Zone: ~${min(low[-10:]):.2f}")
            details["demand_zone"] = round(float(min(low[-10:])), 2)

    except Exception as e:
        details["error"] = str(e)

    return patterns, details

def get_fundamentals_score(ticker):
    """
    Warren Buffett style fundamentals check.
    PE ratio, earnings growth, revenue growth, debt levels.
    """
    score = 0
    details = {}

    try:
        stock = yf.Ticker(ticker)
        info  = stock.info

        pe    = info.get("trailingPE")
        fpe   = info.get("forwardPE")
        eps_g = info.get("earningsGrowth")
        rev_g = info.get("revenueGrowth")
        debt  = info.get("debtToEquity")
        roe   = info.get("returnOnEquity")

        details["pe"]       = pe
        details["fwd_pe"]   = fpe
        details["eps_growth"]= eps_g
        details["rev_growth"]= rev_g
        details["debt_eq"]  = debt
        details["roe"]      = roe

        # Good PE (not overvalued)
        if pe and 0 < pe < 30:
            score += 1
            details["pe_signal"] = f"✅ PE {pe:.1f} — reasonable"
        elif pe and pe > 50:
            details["pe_signal"] = f"⚠️ PE {pe:.1f} — expensive"

        # Growing earnings
        if eps_g and eps_g > 0.10:
            score += 1
            details["earnings_signal"] = f"✅ Earnings growing {eps_g*100:.0f}%"

        # Growing revenue
        if rev_g and rev_g > 0.05:
            score += 0.5
            details["revenue_signal"] = f"✅ Revenue growing {rev_g*100:.0f}%"

        # Low debt
        if debt and debt < 50:
            score += 0.5
            details["debt_signal"] = f"✅ Low debt/equity: {debt:.0f}%"

        # Strong ROE
        if roe and roe > 0.15:
            score += 0.5

    except Exception as e:
        details["error"] = str(e)

    return min(int(score), 2), details

def analyze_ticker(ticker, name=""):
    """
    Full analysis of a single ticker against your rules.
    Returns complete score and breakdown.
    """
    print(f"  Analyzing {ticker}...")
    result = {
        "ticker":   ticker,
        "name":     name or ticker,
        "score":    0,
        "max":      12,
        "signals":  [],
        "rsi":      {},
        "ma":       {},
        "ad":       {},
        "patterns": [],
        "fundamentals": {},
        "buy_rating": "",
        "conviction": "",
    }

    try:
        # RSI Score (0-3)
        rsi_score, rsi_details, oversold_count = get_rsi_score(ticker)
        result["score"] += rsi_score
        result["rsi"]    = rsi_details
        if rsi_score > 0:
            tfs = [tf for tf, d in rsi_details.items() if d.get("oversold")]
            result["signals"].append(f"RSI oversold on {', '.join(tfs).upper()}")

        # MA Score (0-3)
        ma_score, ma_details = get_ma_score(ticker)
        result["score"] += ma_score
        result["ma"]     = ma_details
        if ma_details.get("cross"):
            result["signals"].append(ma_details["cross"])
        if ma_details.get("ma_signal"):
            result["signals"].append(ma_details["ma_signal"])

        # A/D Score (0-1)
        ad_score, ad_details = get_ad_score(ticker)
        result["score"] += ad_score
        result["ad"]     = ad_details
        if ad_score > 0:
            result["signals"].append("A/D rising — institutions loading")

        # Pattern Score (0-2)
        patterns, pat_details = detect_patterns(ticker)
        result["patterns"] = patterns
        if patterns:
            result["score"] += min(len(patterns), 2)
            result["signals"].extend(patterns[:2])

        # Fundamentals Score (0-2)
        fund_score, fund_details = get_fundamentals_score(ticker)
        result["score"] += fund_score
        result["fundamentals"] = fund_details

        # Buy Rating based on YOUR scoring system
        s = result["score"]
        if s >= 7:
            result["buy_rating"]  = "🔥 STRONG BUY"
            result["conviction"]  = "HIGH"
        elif s >= 5:
            result["buy_rating"]  = "⚡ GOOD SETUP"
            result["conviction"]  = "MEDIUM"
        elif s >= 3:
            result["buy_rating"]  = "👀 WATCH"
            result["conviction"]  = "LOW"
        else:
            result["buy_rating"]  = "⏳ NOT YET"
            result["conviction"]  = "SKIP"

        # Get current price
        try:
            data = yf.download(ticker, period="1d", interval="1m", progress=False)
            if not data.empty:
                result["price"] = round(float(data["Close"].iloc[-1]), 2)
        except:
            pass

    except Exception as e:
        result["error"] = str(e)

    return result
