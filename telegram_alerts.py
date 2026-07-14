# telegram_alerts.py — Professional, clean signal format
import requests
import os
from datetime import datetime, timezone

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
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
                chg   = meta.get("regularMarketChangePercent", 0)
                if price:
                    return round(float(price), 2), round(float(chg), 2)
    except:
        pass
    return None, None

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
        return "2-6 months", "Long Term Investment", "RSI > 70 weekly or thesis breaks"
    elif weekly_os and daily_os:
        return "2-6 weeks", "Swing Trade", "RSI > 65 daily or +15-20%"
    elif golden_cross:
        return "1-3 months", "Momentum Trade", "Death Cross or RSI > 75 weekly"
    elif daily_os:
        return "5-15 days", "Short Swing", "RSI > 60 daily or +8-12%"
    elif score >= 5:
        return "1-2 weeks", "Technical Setup", "Break below support or +5-8%"
    else:
        return "2-7 days", "News Play", "Catalyst plays out"

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
    rsi_data = result.get("rsi", {})
    ma_data  = result.get("ma", {})
    patterns = result.get("patterns", [])

    # Price
    price, price_chg = get_current_price(ticker)
    price_str = f"${price:,.2f}  ({price_chg:+.2f}%)" if price else "—"

    # Hold
    duration, trade_type, exit_rule = get_hold_duration(result)

    # Rating — clean text only
    if score >= 7:
        rating = "STRONG BUY"
    elif score >= 5:
        rating = "BUY"
    else:
        rating = "WATCH"

    # RSI — clean one line per timeframe
    rsi_lines = []
    for tf, d in rsi_data.items():
        if isinstance(d, dict) and d.get("rsi"):
            flag = " [OVERSOLD]" if d.get("strong_oversold") else (" [oversold]" if d.get("oversold") else "")
            rsi_lines.append(f"  {tf.upper():<8}{d['rsi']}{d.get('direction','')} {flag}".rstrip())

    rsi_block = "\n".join(rsi_lines) if rsi_lines else "  No data"

    # MA block
    ma_signal  = ma_data.get("ma_signal", "—").replace("✅ ", "").replace("⚠️ ", "")
    cross      = ma_data.get("cross", "").replace("🔥 ", "").replace("✅ ", "").replace("🔴 ", "")
    ma50       = ma_data.get("ma50", "—")
    ma200      = ma_data.get("ma200", "—")

    # Top pattern — clean text
    clean_patterns = []
    for p in patterns[:3]:
        clean = p.replace("📍 ", "").replace("🚀 ", "").replace("🔊 ", "").replace("📊 ", "").replace("⚡ ", "").replace("⚠️ ", "").replace("📈 ", "").replace("📉 ", "").replace("🌟 ", "").replace("📐 ", "").replace("🟢 ", "")
        clean_patterns.append(clean)

    pattern_block = "\n  ".join(clean_patterns) if clean_patterns else "None detected"

    # News — clean
    news_line = news_signals[0]["title"][:80] if news_signals else "No recent news"

    # Timestamp
    now = datetime.now(timezone.utc).strftime("%d %b %Y  %H:%M UTC")

    msg = (
        f"<code>MERITQUANT SCANNER  |  {now}</code>\n"
        f"<code>{'─'*38}</code>\n"
        f"\n"
        f"<b>{rating}</b>  |  <b>${ticker}</b>  |  Score {score}/12\n"
        f"{name}  |  {price_str}\n"
        f"\n"
        f"<code>HOLD     {duration}  |  {trade_type}</code>\n"
        f"<code>Entry    Now or on dip</code>\n"
        f"<code>Exit     {exit_rule}</code>\n"
        f"\n"
        f"<code>RSI</code>\n"
        f"<code>{rsi_block}</code>\n"
        f"\n"
        f"<code>MA       {ma_signal}</code>\n"
    )

    if cross:
        msg += f"<code>         {cross}</code>\n"

    msg += (
        f"<code>         50MA ${ma50}  |  200MA ${ma200}</code>\n"
        f"\n"
        f"<code>PATTERN  {pattern_block}</code>\n"
        f"<code>NEWS     {news_line}</code>\n"
        f"<code>MACRO    {macro_env.replace(' 🟢','').replace(' 🔴','').replace(' 🟡','').replace(' 🟠','').replace(' ⚪','')}</code>\n"
        f"\n"
        f"<i>Not financial advice</i>"
    )

    return msg

def format_scan_summary(results, macro):
    strong = [r for r in results if r["score"] >= 7]
    good   = [r for r in results if 5 <= r["score"] < 7]
    watch  = [r for r in results if 3 <= r["score"] < 5]

    vix       = macro.get("vix", {})
    dxy       = macro.get("dxy", {})
    bonds     = macro.get("bonds", {})
    fred      = macro.get("fred", {})
    bond_etfs = macro.get("bond_etfs", {})

    tlt = bond_etfs.get("TLT", {})
    edv = bond_etfs.get("EDV", {})
    iev = bond_etfs.get("IEV", {})

    def tickers(lst):
        return "  ".join([f"${r['ticker']}" for r in lst[:6]]) or "—"

    now = datetime.now(timezone.utc).strftime("%d %b %Y  %H:%M UTC")
    env = macro.get('environment','?').replace(' 🟢','').replace(' 🔴','').replace(' 🟡','').replace(' 🟠','').replace(' ⚪','')

    return (
        f"<code>MERITQUANT SCANNER  |  {now}</code>\n"
        f"<code>{'─'*38}</code>\n"
        f"\n"
        f"<b>MACRO  |  {env}</b>\n"
        f"\n"
        f"<code>VIX      {vix.get('value','N/A')}  ({vix.get('change',0):+.1f}%)</code>\n"
        f"<code>DXY      {dxy.get('value','N/A')}  ({dxy.get('change',0):+.2f}%)</code>\n"
        f"<code>10yr     {bonds.get('yield_10yr','N/A')}%  |  Fed {fred.get('fed_rate',{}).get('value','N/A')}%</code>\n"
        f"\n"
        f"<code>BONDS</code>\n"
        f"<code>TLT      ${tlt.get('price','N/A')}  ({tlt.get('change',0):+.2f}%)</code>\n"
        f"<code>EDV      ${edv.get('price','N/A')}  ({edv.get('change',0):+.2f}%)</code>\n"
        f"<code>IEV      ${iev.get('price','N/A')}  ({iev.get('change',0):+.2f}%)</code>\n"
        f"\n"
        f"<code>{'─'*38}</code>\n"
        f"<b>STRONG BUY</b>   {tickers(strong)}\n"
        f"<b>BUY</b>          {tickers(good)}\n"
        f"<b>WATCH</b>        {tickers(watch)}\n"
        f"\n"
        f"<code>{len(results)} stocks scanned</code>\n"
        f"<i>Not financial advice</i>"
    )

def send_startup_message():
    now = datetime.now(timezone.utc).strftime("%d %b %Y  %H:%M UTC")
    send(
        f"<code>MERITQUANT SCANNER  |  {now}</code>\n"
        f"<code>{'─'*38}</code>\n"
        f"\n"
        f"<b>SYSTEM ONLINE</b>\n"
        f"\n"
        f"<code>Rules loaded</code>\n"
        f"<code>RSI        Daily + Weekly timeframes</code>\n"
        f"<code>MA         50 + 200  |  Golden/Death Cross</code>\n"
        f"<code>A/D        Institutional flow</code>\n"
        f"<code>Patterns   FVG, Breakout, Fib, S&R</code>\n"
        f"<code>Macro      VIX, DXY, TLT, EDV, IEV, Yields</code>\n"
        f"<code>News       Ticker-specific catalysts</code>\n"
        f"\n"
        f"<code>Scoring    7+ Strong Buy  |  5-6 Buy  |  3-4 Watch</code>\n"
        f"<code>Schedule   5x daily  |  IST timezone</code>\n"
        f"\n"
        f"<i>Not financial advice</i>"
    )
