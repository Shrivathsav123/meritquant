# telegram_alerts.py — Format and send trading alerts
import requests
import os

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

def send(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[Telegram] {text[:100]}"); return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"[Telegram] Error: {e}"); return False


def format_stock_alert(result, news_signals=[], macro_env="NEUTRAL"):
    """Format a complete stock analysis alert."""
    ticker    = result["ticker"]
    name      = result["name"]
    score     = result["score"]
    rating    = result["buy_rating"]
    price     = result.get("price", "?")
    signals   = result.get("signals", [])
    patterns  = result.get("patterns", [])
    rsi_data  = result.get("rsi", {})
    ma_data   = result.get("ma", {})
    fund      = result.get("fundamentals", {})

    # RSI summary
    rsi_lines = []
    for tf, d in rsi_data.items():
        if isinstance(d, dict) and d.get("rsi"):
            oversold = "🔥" if d.get("strong_oversold") else ("⚡" if d.get("oversold") else "")
            rsi_lines.append(f"  {tf.upper()}: {d['rsi']} {d.get('direction','')} {oversold}")

    # Pattern summary
    pattern_lines = [f"  • {p}" for p in patterns[:4]]

    # News summary
    news_lines = []
    for n in news_signals[:2]:
        emoji = "✅" if n["score"] > 0 else "⚠️"
        news_lines.append(f"  {emoji} {n['title'][:70]}")

    # Fundamentals
    fund_lines = []
    if fund.get("pe"): fund_lines.append(f"PE: {fund['pe']:.1f}")
    if fund.get("eps_growth"): fund_lines.append(f"EPS growth: {fund['eps_growth']*100:.0f}%")
    if fund.get("rev_growth"): fund_lines.append(f"Rev growth: {fund['rev_growth']*100:.0f}%")

    msg = f"""
{'='*40}
{rating} — <b>${ticker}</b> | Score: {score}/12
<b>{name}</b> | ${price}
Macro: {macro_env}
{'='*40}

📊 <b>RSI Across Timeframes:</b>
{chr(10).join(rsi_lines) if rsi_lines else "  No RSI data"}

📈 <b>Moving Averages:</b>
  {ma_data.get('ma_signal', '—')}
  {ma_data.get('cross', '')}
  50MA: ${ma_data.get('ma50','?')} | 200MA: ${ma_data.get('ma200','?')}

💡 <b>Key Signals:</b>
{chr(10).join(f'  • {s}' for s in signals[:4]) if signals else '  None'}

📐 <b>Chart Patterns:</b>
{chr(10).join(pattern_lines) if pattern_lines else '  No patterns detected'}

📰 <b>News Catalysts:</b>
{chr(10).join(news_lines) if news_lines else '  No recent news'}

💼 <b>Fundamentals:</b>
  {' | '.join(fund_lines) if fund_lines else 'No data'}

⚠️ Not financial advice | Track record building
""".strip()

    return msg


def format_scan_summary(results, macro):
    """Daily scan summary digest."""
    strong = [r for r in results if r["score"] >= 7]
    good   = [r for r in results if 5 <= r["score"] < 7]
    watch  = [r for r in results if 3 <= r["score"] < 5]

    strong_list = ", ".join([f"${r['ticker']}" for r in strong[:5]])
    good_list   = ", ".join([f"${r['ticker']}" for r in good[:5]])
    watch_list  = ", ".join([f"${r['ticker']}" for r in watch[:5]])

    return f"""
🔍 <b>ALPHA SCANNER — DAILY DIGEST</b>
Macro: {macro.get('environment','?')}
VIX: {macro.get('vix',{}).get('value','?')} | DXY: {macro.get('dxy',{}).get('value','?')} | 10yr: {macro.get('bonds',{}).get('yield_10yr','?')}%

🔥 <b>STRONG BUY (7+):</b> {strong_list or 'None'}
⚡ <b>GOOD SETUP (5-6):</b> {good_list or 'None'}
👀 <b>WATCH (3-4):</b> {watch_list or 'None'}

Total scanned: {len(results)} stocks/ETFs
⚠️ Not financial advice
""".strip()


def send_startup_message():
    send("""
🚀 <b>ALPHA SCANNER — ONLINE</b>

Your trading rules are loaded:
✅ RSI (3H, 4H, Daily, Weekly, Monthly)
✅ 200MA + 50MA + Golden/Death Cross
✅ Accumulation/Distribution
✅ Chart Patterns (FVG, Breakout, S&R, Volume)
✅ Fundamentals (Warren Buffett style)
✅ Macro (VIX, DXY, Bond Yields, CPI)
✅ News catalysts (Trump, contracts, earnings)

Scoring system:
🔥 7+ = STRONG BUY
⚡ 5-6 = GOOD SETUP
👀 3-4 = WATCH

Scanning NYSE + NSE every 4 hours.
Zero cost. Building your track record. 📈
""".strip())
