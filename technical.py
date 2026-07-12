# technical.py — Uses direct HTTP to Yahoo Finance with browser headers
# This bypasses rate limiting by mimicking a real browser request
import pandas as pd
import numpy as np
import ta
import requests
import io
import time
from datetime import datetime, timedelta

RSI_OVERSOLD_STRONG = 35
RSI_OVERSOLD_MAX    = 50

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

def fetch_yahoo(ticker, period="1y", interval="1d"):
    """
    Fetch stock data directly from Yahoo Finance API.
    Uses browser headers to avoid rate limiting.
    """
    try:
        time.sleep(0.5)
        
        # Convert period to timestamps
        end_ts   = int(datetime.now().timestamp())
        days_map = {"3mo": 90, "6mo": 180, "1y": 365, "2y": 730, "5y": 1825}
        days     = days_map.get(period, 365)
        start_ts = int((datetime.now() - timedelta(days=days)).timestamp())

        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            f"?period1={start_ts}&period2={end_ts}&interval={interval}"
            f"&includePrePost=false&events=div%7Csplit"
        )

        session = requests.Session()
        session.headers.update(HEADERS)
        
        # First get cookies
        session.get("https://finance.yahoo.com", timeout=10)
        
        resp = session.get(url, timeout=15)
        if resp.status_code != 200:
            return pd.DataFrame()

        data   = resp.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            return pd.DataFrame()

        chart     = result[0]
        timestamps = chart.get("timestamp", [])
        quote     = chart.get("indicators", {}).get("quote", [{}])[0]

        if not timestamps:
            return pd.DataFrame()

        df = pd.DataFrame({
            "Open":   quote.get("open",   [None]*len(timestamps)),
            "High":   quote.get("high",   [None]*len(timestamps)),
            "Low":    quote.get("low",    [None]*len(timestamps)),
            "Close":  quote.get("close",  [None]*len(timestamps)),
            "Volume": quote.get("volume", [0]*len(timestamps)),
        }, index=pd.to_datetime(timestamps, unit="s"))

        df = df.dropna(subset=["Close"])
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

    checks = [
        ("daily",  "1y",  "1d"),
        ("weekly", "2y",  "1wk"),
    ]

    for tf_name, period, interval in checks:
        try:
            data = fetch_yahoo(ticker, period=period, interval=interval)
            if data.empty or len(data) < 15:
                details[tf_name] = {"rsi": None, "note": "no data"}
                continue

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
        data = fetch_yahoo(ticker, period="2y", interval="1d")
        if data.empty or len(data) < 50:
            return 0, {"note": "insufficient data"}

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
                    details["cross"] = "🔥 GOLDEN CROSS!"
                elif active_golden:
                    score += 1
                    details["cross"] = "✅ Golden Cross active"
                elif death_cross:
                    details["cross"] = "🔴 DEATH CROSS"

        if ma200 and abs(current_price - ma200) / ma200 < 0.02:
            score += 1
            details["near_200ma"] = "📍 At 200MA"

    except Exception as e:
        details["error"] = str(e)

    return score, details

def get_ad_score(ticker):
    score   = 0
    details = {}

    try:
        data = fetch_yahoo(ticker, period="3mo", interval="1d")
        if data.empty or len(data) < 20:
            return 0, {}

        ad_line   = get_ad(data["High"], data["Low"], data["Close"], data["Volume"])
        recent_ad = ad_line.tail(5)
        ad_rising = recent_ad.iloc[-1] > recent_ad.iloc[0]

        if ad_rising:
            score = 1
            details["signal"] = "✅ Institutions accumulating"
        else:
            details["signal"] = "⚠️ A/D declining"

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
        data = fetch_yahoo(ticker, period="6mo", interval="1d")
        if data.empty or len(data) < 20:
            return patterns, details

        close   = data["Close"].values
        high    = data["High"].values
        low     = data["Low"].values
        volume  = data["Volume"].values
        current = close[-1]
        avg_vol = volume[-20:].mean()

        # Support & Resistance
        recent_high = max(high[-20:])
        recent_low  = min(low[-20:])

        if abs(current - recent_low) / current < 0.03:
            patterns.append(f"📍 Near Support: ${recent_low:.2f}")

        if abs(current - recent_high) / current < 0.02:
            patterns.append(f"🚀 Testing Resistance: ${recent_high:.2f}")

        # Breakout
        if len(high) > 21 and current > max(high[-21:-1]) and avg_vol > 0 and volume[-1] > avg_vol * 1.5:
            patterns.append("🚀 BREAKOUT with volume!")

        # Volume Spike
        if avg_vol > 0 and volume[-1] > avg_vol * 2:
            patterns.append(f"🔊 Volume Spike: {volume[-1]/avg_vol:.1f}x")

        # Fibonacci
        if len(high) >= 50:
            fibs = get_fibonacci_levels(max(high[-50:]), min(low[-50:]))
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
            if high[-1] > high[-5] > high[-10] and low[-1] > low[-5] > low[-10]:
                patterns.append("📈 Uptrend: Higher Highs & Lows")
            elif high[-1] < high[-5] and low[-1] < low[-5]:
                patterns.append("📉 Downtrend: Lower Highs & Lows")

        # Fair Value Gap — 3-candle structural imbalance with ATR depth + volume filters
        open_ = data["Open"].values
        atr_s = pd.Series(
            pd.concat([
                data["High"] - data["Low"],
                (data["High"] - data["Close"].shift()).abs(),
                (data["Low"]  - data["Close"].shift()).abs(),
            ], axis=1).max(axis=1)
        ).rolling(14).mean().values
        vol_ma_20 = pd.Series(volume.astype(float)).rolling(20).mean().values

        for i in range(2, len(close)):
            gap_bottom = high[i - 2]
            gap_top    = low[i]
            if gap_top <= gap_bottom:
                continue
            if i < len(close) - 30:       # only recent FVGs
                continue
            gap_pct  = (gap_top - gap_bottom) / max(close[i - 2], 1)
            if gap_pct < 0.001:
                continue
            b_body   = abs(close[i - 1] - open_[i - 1])
            atr_val  = atr_s[i - 1]
            depth_ok = not np.isnan(atr_val) and atr_val > 0 and b_body > 1.5 * atr_val
            vm       = vol_ma_20[i - 1]
            vol_ok   = not np.isnan(vm) and vm > 0 and volume[i - 1] > vm
            label    = f"📊 Bullish FVG ${gap_bottom:.2f}–${gap_top:.2f}"
            if depth_ok and vol_ok:
                label += " ✅ depth+vol confirmed"
            elif depth_ok:
                label += " ✅ depth confirmed"
            patterns.append(label)
            break

    except Exception as e:
        details["error"] = str(e)

    return patterns, details

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
        rsi_score, rsi_details, _ = get_rsi_score(ticker)
        result["score"] += rsi_score
        result["rsi"]    = rsi_details
        if rsi_score > 0:
            tfs = [tf for tf, d in rsi_details.items()
                   if isinstance(d, dict) and d.get("oversold")]
            if tfs:
                result["signals"].append(f"RSI oversold on {', '.join(tfs).upper()}")

        ma_score, ma_details = get_ma_score(ticker)
        result["score"] += ma_score
        result["ma"]     = ma_details
        if ma_details.get("cross"):
            result["signals"].append(ma_details["cross"])
        if ma_details.get("ma_signal"):
            result["signals"].append(ma_details["ma_signal"])

        ad_score, ad_details = get_ad_score(ticker)
        result["score"] += ad_score
        result["ad"]     = ad_details
        if ad_score > 0:
            result["signals"].append("A/D rising — institutions loading")

        patterns, _ = detect_patterns(ticker)
        result["patterns"] = patterns
        if patterns:
            result["score"] += min(len(patterns), 2)
            result["signals"].extend(patterns[:2])

        # Print debug score
        print(f"    Score: {result['score']} | RSI:{rsi_score} MA:{ma_score} AD:{ad_score} Patterns:{len(patterns)}")

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
        print(f"  Error: {e}")

    return result
