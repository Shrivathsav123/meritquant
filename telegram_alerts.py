# telegram_alerts.py — Clean, beautiful Telegram alerts
import requests
import os

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

def get_current_price(ticker):
    try:
        url  = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1d&interval=1m"
        resp = requests.get(url, headers=HEADERS, timeout=8)
        if resp.status_code == 200:
            data   = resp.json()
            result = data.get("chart", {}).get("result", [])
            if result:
                meta  = result[0].get("meta", {})
                price = meta.get("regularMarketPrice") or meta.get("previousClose")
                if price:
                    return round(float(price), 2)
    except:
        pass
    return None

def get_hold_duration(result):
    rsi_data     = result.get("rsi", {})
    score        = result.get("score", 0)
    ma_data      = result.get("ma", {})
    weekly_os    = isinstance(rsi_data.get("weekly"), dict) and rsi_data["weekly"].get("oversold")
    daily_os     = isinstance(rsi_data.get("daily"),  dict) and rsi_data["daily"].get("oversold")
    weekly_str   = isinstance(rsi_data.get("weekly"), dict) and rsi_data["weekly"].get("strong_oversold")
    daily_str    = isinstance(rsi_data.get("daily"),  dict) and rsi_data["daily"].get("strong_oversold")
    golden_cross = "GOLDEN CROSS" in str(ma_data.get("cross", ""))
    above_200    = ma_data.get("above_200ma", False)

    if weekly_str and daily_str and above_200:
        return "2–6 months", "Long Term Investment", "RSI > 70 weekly or thesis breaks"
    elif weekly_os and daily_os:
        return "2–6 weeks", "Swing Trade", "RSI > 65 daily or +15–20%"
    elif golden_cross:
        return "1–3 months", "Momentum Trade", "Death Cross forms or RSI > 75 weekly"
    elif daily_os:
        return "5–15 days", "Short Swing", "RSI > 60 daily or +8–12%"
    elif score >= 5:
        return "1–2 weeks", "Technical Setup", "Break below support or +5–8%"
    else:
        return "2–7 days", "News Play", "Catalyst plays out"

def send(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[Telegram] {text[:100]}")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"[Telegram] Error: {e}")
        return False

def format_stock_alert(result, news_signals=[], macro_env="NEUTRAL"):
    ticker   = result["ticker"]
    name     = result["name"]
    score    = result["score"]
    rating   = result["buy_rating"]
    rsi_data = result.get("rsi", {})
    ma_data  = result.get("ma", {})
    patterns = result.get("patterns", [])

    # Price
    price     = get_current_price(ticker)
    price_str = f"${price:,.2f}" if price else "—"

    # Hold
    duration, trade_type, exit_rule = get_hold_duration(result)

    # Rating emoji only
    rating_emoji = "🔥" if score >= 7 else "⚡" if score >= 5 else "👀"

    # RSI per timeframe — clean one liner
    rsi_parts = []
    for tf, d in rsi_data.items():
        if isinstance(d, dict) and d.get("rsi"):
            flag = "🔥" if d.get("strong_oversold") else "⚡" if d.get("oversold") else ""
            rsi_parts.append(f"{tf.upper()} {d['rsi']} {d.get('direction','')} {flag}".strip())
    rsi_line = " │ ".join(rsi_parts) if rsi_parts else "No data"

    # MA clean line
    ma_line   = ma_data.get("ma_signal", "—")
    cross_line = ma_data.get("cross", "")
    ma50      = ma_data.get("ma50", "—")
    ma200     = ma_data.get("ma200", "—")

    # Top pattern only
    top_pattern = patterns[0] if patterns else "—"

    # Top news only
    top_news = news_signals[0]["title"][:80] if news_signals else "No recent news"
    news_sentiment = "✅" if news_signals and news_signals[0].get("score", 0) > 0 else "📰"

    msg = (
        f"{rating_emoji} <b>${ticker}</b>  ·  {name}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>{price_str}</b>  ·  Score <b>{score}/12</b>  ·  {macro_env}\n"
        f"\n"
        f"⏱ <b>{duration}</b>  ·  {trade_type}\n"
        f"🟢 Entry  →  now or on dip\n"
        f"🔴 Exit   →  {exit_rule}\n"
        f"\n"
        f"📊 RSI  ·  {rsi_line}\n"
        f"📈 MA   ·  {ma_line}\n"
    )

    if cross_line:
        msg += f"         {cross_line}\n"

    msg += (
        f"         50MA ${ma50}  ·  200MA ${ma200}\n"
        f"\n"
        f"📐 Pattern  ·  {top_pattern}\n"
        f"{news_sentiment} News  ·  {top_news}\n"
        f"\n"
        f"<i>⚠️ Not financial advice</i>"
    )

    return msg

def format_scan_summary(results, macro):
    strong = [r for r in results if r["score"] >= 7]
    good   = [r for r in results if 5 <= r["score"] < 7]
    watch  = [r for r in results if 3 <= r["score"] < 5]

    vix   = macro.get("vix", {})
    dxy   = macro.get("dxy", {})
    bonds = macro.get("bonds", {})
    fred  = macro.get("fred", {})

    def fmt(lst):
        return "  ".join([f"<b>${r['ticker']}</b>" for r in lst[:5]]) or "—"

    vix_val   = vix.get('value', 'N/A')
    dxy_val   = dxy.get('value', 'N/A')
    yield_val = bonds.get('yield_10yr', 'N/A')
    fed_val   = fred.get('fed_rate', {}).get('value', 'N/A')

    vix_sig   = "🟢" if isinstance(vix_val, float) and vix_val < 20 else "🔴" if isinstance(vix_val, float) and vix_val > 30 else "🟡"
    dxy_chg   = dxy.get('change', 0)
    dxy_sig   = "🟢" if isinstance(dxy_chg, float) and dxy_chg < -0.3 else "🔴" if isinstance(dxy_chg, float) and dxy_chg > 0.3 else "🟡"

    return (
        f"🔍 <b>ALPHA SCANNER  ·  DAILY DIGEST</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌍 Macro  ·  {macro.get('environment','?')}\n"
        f"\n"
        f"{vix_sig} VIX {vix_val}  ·  "
        f"{dxy_sig} DXY {dxy_val}  ·  "
        f"📉 Yield {yield_val}%  ·  "
        f"🏦 Fed {fed_val}%\n"
        f"\n"
        f"🔥 Strong Buy  ·  {fmt(strong)}\n"
        f"⚡ Good Setup  ·  {fmt(good)}\n"
        f"👀 Watch       ·  {fmt(watch)}\n"
        f"\n"
        f"<i>{len(results)} scanned  ·  ⚠️ Not financial advice</i>"
    )

def send_startup_message():
    send(
        "🚀 <b>ALPHA SCANNER  ·  ONLINE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Rules loaded:\n"
        "✅ RSI  ·  Daily + Weekly\n"
        "✅ MA   ·  50 + 200  ·  Golden/Death Cross\n"
        "✅ A/D  ·  Institutional flow\n"
        "✅ Patterns  ·  FVG, Breakout, Fib, S&R\n"
        "✅ Macro  ·  VIX, DXY, Yields, CPI, Fed\n"
        "✅ News  ·  Ticker-specific catalysts\n"
        "\n"
        "Every alert includes:\n"
        "💰 Real-time price\n"
        "⏱ Hold duration + trade type\n"
        "🟢 Entry  ·  🔴 Exit strategy\n"
        "\n"
        "🔥 7+  ·  Strong Buy\n"
        "⚡ 5–6  ·  Good Setup\n"
        "👀 3–4  ·  Watch\n"
        "\n"
        "<i>Scanning 5× daily. Building your track record.</i>"
    )
