# macro.py — VIX, DXY, Bond Yields, CPI, Fed statements
import yfinance as yf
import requests
import feedparser
from datetime import datetime, timedelta

def get_macro_environment():
    """
    Scan all macro indicators.
    Your rules:
    - VIX up = equities down, VIX down = equities up
    - DXY up = equities/emerging markets down
    - Bond yields up = pressure on growth stocks
    - CPI higher than expected = Fed may hike = bearish
    """
    macro = {
        "vix":          {},
        "dxy":          {},
        "bonds":        {},
        "environment":  "NEUTRAL",
        "score_modifier": 0,  # +ve = boost signals, -ve = reduce conviction
        "summary":      [],
        "alerts":       [],
    }

    bullish_count = 0
    bearish_count = 0

    # ── VIX ──────────────────────────────────────────────────
    try:
        vix = yf.download("^VIX", period="5d", interval="1d", progress=False)
        if not vix.empty:
            current_vix = float(vix["Close"].iloc[-1])
            prev_vix    = float(vix["Close"].iloc[-2])
            vix_change  = ((current_vix - prev_vix) / prev_vix) * 100

            macro["vix"] = {
                "value":  round(current_vix, 2),
                "change": round(vix_change, 1),
                "signal": ""
            }

            if current_vix < 15:
                macro["vix"]["signal"] = "✅ VIX LOW — Risk ON, equities bullish"
                bullish_count += 2
                macro["summary"].append(f"VIX {current_vix:.1f} — Very low fear, risk on")
            elif current_vix < 20:
                macro["vix"]["signal"] = "✅ VIX CALM — Mild risk on"
                bullish_count += 1
                macro["summary"].append(f"VIX {current_vix:.1f} — Calm market")
            elif current_vix < 30:
                macro["vix"]["signal"] = "⚠️ VIX ELEVATED — Caution"
                bearish_count += 1
                macro["summary"].append(f"VIX {current_vix:.1f} — Elevated fear")
            else:
                macro["vix"]["signal"] = "🔴 VIX HIGH — Risk OFF, reduce exposure"
                bearish_count += 2
                macro["alerts"].append(f"⚠️ VIX SPIKE: {current_vix:.1f} — Market in fear")
                macro["summary"].append(f"VIX {current_vix:.1f} — High fear, risk off")

            if vix_change > 15:
                macro["alerts"].append(f"🚨 VIX SURGING +{vix_change:.1f}% — Volatility spike!")
            elif vix_change < -10:
                macro["alerts"].append(f"✅ VIX FALLING {vix_change:.1f}% — Fear subsiding")

    except Exception as e:
        macro["vix"]["error"] = str(e)

    # ── DXY ──────────────────────────────────────────────────
    try:
        dxy = yf.download("DX-Y.NYB", period="5d", interval="1d", progress=False)
        if not dxy.empty:
            current_dxy = float(dxy["Close"].iloc[-1])
            prev_dxy    = float(dxy["Close"].iloc[-2])
            dxy_change  = ((current_dxy - prev_dxy) / prev_dxy) * 100

            macro["dxy"] = {
                "value":  round(current_dxy, 2),
                "change": round(dxy_change, 1),
                "signal": ""
            }

            if dxy_change < -0.3:
                macro["dxy"]["signal"] = "✅ DXY FALLING — Bullish for stocks & emerging markets (NSE)"
                bullish_count += 1
                macro["summary"].append(f"DXY {current_dxy:.1f} falling — good for equities")
            elif dxy_change > 0.3:
                macro["dxy"]["signal"] = "⚠️ DXY RISING — Pressure on equities, bad for NSE/emerging"
                bearish_count += 1
                macro["summary"].append(f"DXY {current_dxy:.1f} rising — headwind for stocks")
            else:
                macro["dxy"]["signal"] = f"◆ DXY STABLE at {current_dxy:.1f}"

    except Exception as e:
        macro["dxy"]["error"] = str(e)

    # ── Bond Yields (10yr Treasury) ───────────────────────────
    try:
        tnx = yf.download("^TNX", period="5d", interval="1d", progress=False)
        if not tnx.empty:
            current_yield = float(tnx["Close"].iloc[-1])
            prev_yield    = float(tnx["Close"].iloc[-2])
            yield_change  = current_yield - prev_yield

            macro["bonds"] = {
                "yield_10yr": round(current_yield, 3),
                "change":     round(yield_change, 3),
                "signal":     ""
            }

            if yield_change > 0.05:
                macro["bonds"]["signal"] = "⚠️ YIELDS RISING — Pressure on growth/tech stocks"
                bearish_count += 1
                macro["summary"].append(f"10yr yield {current_yield:.2f}% rising — tech headwind")
            elif yield_change < -0.05:
                macro["bonds"]["signal"] = "✅ YIELDS FALLING — Bullish for growth/tech"
                bullish_count += 1
                macro["summary"].append(f"10yr yield {current_yield:.2f}% falling — bullish tech")

            if current_yield > 4.5:
                macro["alerts"].append(f"⚠️ High yields ({current_yield:.2f}%) — headwind for stocks")

    except Exception as e:
        macro["bonds"]["error"] = str(e)

    # ── CPI/Fed News from Google RSS ──────────────────────────
    try:
        url = "https://news.google.com/rss/search?q=CPI+inflation+Federal+Reserve+interest+rate+2026&hl=en-US&gl=US&ceid=US:en"
        feed = feedparser.parse(url)
        macro_news = []
        for entry in feed.entries[:5]:
            title = entry.get("title", "").lower()
            if any(w in title for w in ["cpi", "inflation", "fed", "rate", "powell", "fomc"]):
                macro_news.append(entry.get("title", ""))
                if any(w in title for w in ["rate cut", "dovish", "pause", "lower inflation"]):
                    bullish_count += 1
                elif any(w in title for w in ["rate hike", "hawkish", "higher inflation", "hot cpi"]):
                    bearish_count += 1
        macro["fed_news"] = macro_news[:3]
    except:
        macro["fed_news"] = []

    # ── Overall Environment ───────────────────────────────────
    net = bullish_count - bearish_count
    if net >= 3:
        macro["environment"]    = "RISK ON 🟢"
        macro["score_modifier"] = +1
    elif net >= 1:
        macro["environment"]    = "MILDLY BULLISH 🟡"
        macro["score_modifier"] = 0
    elif net <= -3:
        macro["environment"]    = "RISK OFF 🔴"
        macro["score_modifier"] = -1
    elif net <= -1:
        macro["environment"]    = "CAUTIOUS 🟠"
        macro["score_modifier"] = -1
    else:
        macro["environment"]    = "NEUTRAL ⚪"
        macro["score_modifier"] = 0

    return macro


def format_macro_alert(macro):
    """Format macro environment for Telegram."""
    env   = macro["environment"]
    vix   = macro.get("vix", {})
    dxy   = macro.get("dxy", {})
    bonds = macro.get("bonds", {})

    lines = [
        f"🌍 <b>MACRO ENVIRONMENT: {env}</b>",
        "",
        f"😱 VIX: {vix.get('value','?')} ({vix.get('change',0):+.1f}%) — {vix.get('signal','')}",
        f"💵 DXY: {dxy.get('value','?')} ({dxy.get('change',0):+.1f}%) — {dxy.get('signal','')}",
        f"📉 10yr Yield: {bonds.get('yield_10yr','?')}% ({bonds.get('change',0):+.3f}%) — {bonds.get('signal','')}",
    ]

    if macro.get("fed_news"):
        lines.append("")
        lines.append("📰 <b>Fed/CPI News:</b>")
        for n in macro["fed_news"][:2]:
            lines.append(f"• {n[:80]}")

    if macro.get("alerts"):
        lines.append("")
        lines.append("🚨 <b>Macro Alerts:</b>")
        for a in macro["alerts"]:
            lines.append(f"• {a}")

    return "\n".join(lines)
