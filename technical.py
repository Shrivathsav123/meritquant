# technical.py — Trading rules engine using Stooq (no rate limits)
import pandas as pd
import numpy as np
import ta
import time
import requests
from datetime import datetime, timedelta

# ── Your RSI Rules ────────────────────────────────────────────
RSI_OVERSOLD_STRONG = 35
RSI_OVERSOLD_MAX    = 50
RSI_OVERBOUGHT      = 70

def stooq_ticker(ticker):
    if ticker.endswith(".NS"):
        return ticker.replace(".NS", ".IN")
    fixes = {
        "^VIX":    "^VIX",
        "^TNX":    "^TNX",
        "^GSPC":   "^SPX",
        "^IXIC":   "^NDQ",
        "^DJI":    "^DJI",
        "DX-Y.NYB":"UUP",
    }
    return fixes.get(ticker, ticker)

def safe_download(ticker, period_days=500):
    try:
        from pandas_datareader import data as pdr
        end   = datetime.today()
        start = end - timedelta(days=period_days)
        stk   = stooq_ticker(ticker)
        df    = pdr.DataReader(stk, "stooq", start=start, end=end)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.sort_index()
        df.columns = [c.capitalize() for c in df.columns]
        return df
    except Exception as e:
        return pd.DataFrame()

def get_rsi(series, period=14):
    try:
        return ta.momentum.RSIIndicator(series, window=period).rsi()
    except:
        return pd.Series([50] * len(series))

def get_ad(high, low, close, volume):
    try:
        return ta.volume.AccDistIndexIndicator(high, low, close, volume).acc_dist_index()
    except:
        return pd.Series([0] * len(close))

def get_rsi_score(ticker):
    score          = 0
    details        = {}
    oversold_count = 0

    timeframe_checks = [
        ("daily",  500),
        ("weekly", 1000),
    ]

    for tf_name, period_days in timeframe_checks:
        try:
            data = safe_download(ticker, period_days=period_days)
            if data.empty or len(data) < 15:
                continue

            if tf_name == "weekly":
                data = data.resample("W").agg({
                    "Open":  "first",
                    "High":  "max",
                    "Low":   "min",
                    "Close": "last",
                    "Volume":"sum"
                }).dropna()

            rsi_series  = get_rsi(data["Close"])
            current_rsi = float(rsi_series.iloc[-1])
            prev_rsi    = float(rsi_series.iloc[-2]) if len(rsi_series) > 1 else current_rsi

            details[tf_name] = {
                "rsi":             round(current_rsi, 1),
                "direction":       "↑" if current_rsi > prev_rsi else "↓",
                "oversold":        current_rsi <= RSI_OVERSOLD_MAX,
                "strong_oversold": current_rsi <= RSI_OVERSOLD_STRONG,
            }

            if current_rsi <= RSI_OVERSOLD_STRONG:
                oversold_count += 1
            elif current_rsi <= RSI_OVERSOLD_MAX:
                oversold_count += 0.5

        except Exception as e:
            details[tf_name] = {"rsi": None, "error": str(e)}

    if oversold_count >= 2:
        score = 3
    elif oversold_count >= 1:
        score = 2
    elif oversold_count >= 0.5:
        score = 1

    return score, details, oversold_count

def get_ma_score(ticker):
    score   = 0
    details = {}

    try:
        data = safe_download(ticker, period_days=600)
        if data.empty or len(data) < 50:
            return 0, {}

        close         = data["Close"]
        current_price = float(close.iloc[-1])
        ma50          = float(close.rolling(50).mean().iloc[-1])
        ma200         = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
        ma50_prev     = float(close.rolling(50).mean().iloc[-2]) if len(close) >= 51 else ma50
        ma200_prev    = float(close.rolling(200).mean().iloc[-2]) if len(close) >= 201 else ma200

        details["price"] = round(current_price, 2)
        details["ma50"]  = round(ma50, 2)
        details["ma200"] = round(ma200, 2) if ma200 else None

        if ma200:
            above_200 = current_price > ma200
            details["above_200ma"] = above_200

            if above_200:
                score += 1
                details["ma_signal"] = "✅ Above 200MA"
            else:
                details["ma_signal"] = "⚠️ Below 200MA"

            if ma50_prev and ma200_prev:
                golden_cross  = ma50 > ma200 and ma50_prev <= ma200_prev
                death_cross   = ma50 < ma200 and ma50_prev >= ma200_prev
                active_golden = ma50 > ma200

                if golden_cross:
                    score += 2
                    details["cross"] = "🔥 GOLDEN CROSS just formed!"
                elif active_golden:
                    score += 1
                    details["cross"] = "✅ Golden Cross active"
                elif death_cross:
                    details["cross"] = "🔴 DEATH CROSS just formed"

        if ma200 and abs(current_price - ma200) / ma200 < 0.02:
            score += 1
            details["near_200ma"] = "📍 Price at 200MA key level"

    except Exception as e:
        details["error"] = str(e)

    return score, details

def get_ad_score(ticker):
    score   = 0
    details = {}

    try:
        data = safe_download(ticker, period_days=120)
        if data.empty or len(data) < 20:
            return 0, {}

        ad_line   = get_ad(data["High"], data["Low"], data["Close"], data["Volume"])
        recent_ad = ad_line.tail(5)
        ad_rising = recent_ad.iloc[-1] > recent_ad.iloc[0]

        details["ad_rising"] = ad_rising

        if ad_rising:
            score = 1
            details["signal"] = "✅ Institutions accumulating"
        else:
            details["signal"] = "⚠️ A/D flat/declining"

    except Exception as e:
        details["error"] = str(e)

    return score, details

def get_fibonacci_levels(swing_high, swing_low):
    diff = swing_high - swing_low
    return {
        "38.2%": round(float(swing_high - 0.382 * diff), 2),
        "50.0%": round(float(swing_high - 0.500 * diff), 2),
        "61.8%": round(float(swing_high - 0.618 * diff), 2),
    }

def detect_patterns(ticker):
    patterns = []
    details  = {}

    try:
        data = safe_download(ticker, period_days=200)
        if data.empty or len(data) < 20:
            return patterns, details

        close   = data["Close"].values
        high    = data["High"].values
        low     = data["Low"].values
        volume  = data["Volume"].values
        current = close[-1]

        # Support & Resistance
        recent_high = max(high[-20:])
        recent_low  = min(low[-20:])

        if abs(current - recent_low) / current < 0.03:
            patterns.append(f"📍 Near Support: ${recent_low:.2f}")
            details["support"] = round(float(recent_low), 2)

        if abs(current - recent_high) / current < 0.02:
            patterns.append(f"🚀 Testing Resistance: ${recent_high:.2f}")

        # Breakout with volume
        avg_vol = volume[-20:].mean()
        if len(high) > 21 and current > max(high[-21:-1]) and avg_vol > 0 and volume[-1] > avg_vol * 1.5:
            patterns.append("🚀 BREAKOUT — Above 20D high with volume!")
            details["breakout"] = True

        # Volume Spike
        if avg_vol > 0 and volume[-1] > avg_vol * 2:
            patterns.append(f"🔊 Volume Spike: {volume[-1]/avg_vol:.1f}x average")

        # Fibonacci
        if len(high) >= 50:
            swing_high = max(high[-50:])
            swing_low  = min(low[-50:])
            fibs       = get_fibonacci_levels(swing_high, swing_low)

            for level_name, level_price in fibs.items():
                if level_price > 0 and abs(current - level_price) / level_price < 0.015:
                    emoji = "🌟" if level_name == "61.8%" else "📐"
                    patterns.append(f"{emoji} Fib {level_name}: ${level_price:.2f}")
                    break

        # RSI Divergence
        rsi = get_rsi(pd.Series(close)).values
        if len(rsi) >= 5:
            if close[-1] < close[-5] and rsi[-1] > rsi[-5] and rsi[-1] < 50:
                patterns.append("⚡ Bullish RSI Divergence")
            if close[-1] > close[-5] and rsi[-1] < rsi[-5] and rsi[-1] > 60:
                patterns.append("⚠️ Bearish RSI Divergence")

        # Market Structure
        if len(high) >= 10:
            hh = high[-1] > high[-5] and high[-5] > high[-10]
            hl = low[-1]  > low[-5]  and low[-5]  > low[-10]
            lh = high[-1] < high[-5]
            ll = low[-1]  < low[-5]

            if hh and hl:
                patterns.append("📈 Higher Highs & Lows (Uptrend)")
                details["structure"] = "uptrend"
            elif lh and ll:
                patterns.append("📉 Lower Highs & Lows (Downtrend)")
                details["structure"] = "downtrend"

        # Fair Value Gap
        for i in range(2, len(close)):
            if low[i] > high[i-2] and i >= len(close) - 5:
                patterns.append("📊 Bullish FVG — buy on pullback")
                break

    except Exception as e:
        details["error"] = str(e)

    return patterns, details

def get_fundamentals_score(ticker):
    score   = 0
    details = {}
    try:
        url     = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1d&interval=1d"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp    = requests.get(url, headers=headers, timeout=8)
        if resp.status_code == 200:
            data  = resp.json()
            meta  = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
            price = meta.get("regularMarketPrice", 0)
            if price:
                details["price"] = price
    except Exception as e:
        details["error"] = str(e)
    return min(int(score), 2), details

def analyze_ticker(ticker, name=""):
    print(f"  Analyzing {ticker}...")
    result = {
        "ticker":       ticker,
        "name":         name or ticker,
        "score":        0,
        "max":          12,
        "signals":      [],
        "rsi":          {},
        "ma":           {},
        "ad":           {},
        "patterns":     [],
        "fundamentals": {},
        "buy_rating":   "",
        "conviction":   "",
    }

    try:
        # RSI Score (0-3)
        rsi_score, rsi_details, _ = get_rsi_score(ticker)
        result["score"] += rsi_score
        result["rsi"]    = rsi_details
        if rsi_score > 0:
            tfs = [tf for tf, d in rsi_details.items()
                   if isinstance(d, dict) and d.get("oversold")]
            if tfs:
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
        patterns, _ = detect_patterns(ticker)
        result["patterns"] = patterns
        if patterns:
            result["score"] += min(len(patterns), 2)
            result["signals"].extend(patterns[:2])

        # Fundamentals Score (0-2)
        fund_score, fund_details = get_fundamentals_score(ticker)
        result["score"]        += fund_score
        result["fundamentals"]  = fund_details

        # Buy Rating
        s = result["score"]
        if s >= 7:
            result["buy_rating"] = "🔥 STRONG BUY"
            result["conviction"] = "HIGH"
        elif s >= 5:
            result["buy_rating"] = "⚡ GOOD SETUP"
            result["conviction"] = "MEDIUM"
        elif s >= 3:
            result["buy_rating"] = "👀 WATCH"
            result["conviction"] = "LOW"
        else:
            result["buy_rating"] = "⏳ NOT YET"
            result["conviction"] = "SKIP"

    except Exception as e:
        result["error"] = str(e)
        print(f"  Error analyzing {ticker}: {e}")

    return result
