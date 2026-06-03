# trading/trader.py — Autonomous AI Trading Bot
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from datetime import datetime
from trading.portfolio import (
    load_portfolio, save_portfolio, load_trades,
    update_position_prices, execute_buy, execute_sell,
    check_stop_losses, STARTING_BALANCE
)
from trading.decision_engine import make_trading_decision
from macro import get_macro_environment

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(text)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=10,
        )
    except:
        pass

def get_price(ticker):
    """Get current price from Yahoo Finance."""
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

def get_prices(tickers):
    """Get current prices for a list of tickers."""
    prices = {}
    for ticker in tickers:
        price = get_price(ticker)
        if price:
            prices[ticker] = price
    return prices

def load_scan_results():
    """Load latest scan results from scanner."""
    try:
        path = "data/scan_results.json"
        if os.path.exists(path):
            return json.load(open(path))
    except:
        pass
    return []

def format_trade_alert(action, ticker, price, shares, cost, pnl=None):
    """Format clean professional trade notification."""
    now = datetime.utcnow().strftime("%d %b %Y  %H:%M UTC")
    if action == "BUY":
        return (
            f"<code>TRADE  |  {now}</code>\n"
            f"<code>{'─'*35}</code>\n"
            f"\n"
            f"<b>BUY  |  ${ticker}</b>\n"
            f"<code>Price    ${price:,.2f}</code>\n"
            f"<code>Shares   {shares}</code>\n"
            f"<code>Cost     ${cost:,.0f}</code>\n"
        )
    else:
        pnl_str = f"{pnl:+,.0f}" if pnl else "—"
        return (
            f"<code>TRADE  |  {now}</code>\n"
            f"<code>{'─'*35}</code>\n"
            f"\n"
            f"<b>SELL  |  ${ticker}</b>\n"
            f"<code>Price    ${price:,.2f}</code>\n"
            f"<code>P&L      ${pnl_str}</code>\n"
        )

def format_portfolio_update(portfolio):
    """Format portfolio summary for Telegram."""
    now       = datetime.utcnow().strftime("%d %b %Y  %H:%M UTC")
    positions = portfolio["positions"]
    pnl       = portfolio["pnl"]
    pnl_pct   = portfolio["pnl_pct"]
    wins      = portfolio.get("wins", 0)
    losses    = portfolio.get("losses", 0)
    total     = wins + losses
    win_rate  = round(wins / total * 100) if total > 0 else 0

    pos_lines = ""
    for t, p in positions.items():
        pct = p.get("pnl_pct", 0)
        pos_lines += f"<code>  {t:<6}  ${p.get('current_price',0):>8.2f}  {pct:>+6.1f}%</code>\n"

    return (
        f"<code>PORTFOLIO  |  {now}</code>\n"
        f"<code>{'─'*35}</code>\n"
        f"\n"
        f"<code>Total Value  ${portfolio['total_value']:>12,.0f}</code>\n"
        f"<code>Cash         ${portfolio['cash']:>12,.0f}</code>\n"
        f"<code>P&L          ${pnl:>+12,.0f}  ({pnl_pct:+.1f}%)</code>\n"
        f"<code>Win Rate     {win_rate}%  ({wins}W / {losses}L)</code>\n"
        f"\n"
        f"<b>Open Positions ({len(positions)}):</b>\n"
        f"{pos_lines if pos_lines else '<code>  No open positions</code>\n'}"
        f"\n"
        f"<i>Paper trading — Not financial advice</i>"
    )

def run_trader():
    """Main trading loop."""
    print(f"\n[Trader] Starting — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

    # Load state
    portfolio = load_portfolio()
    print(f"[Trader] Portfolio: ${portfolio['total_value']:,.0f} | Cash: ${portfolio['cash']:,.0f} | Positions: {len(portfolio['positions'])}")

    # Get macro environment
    print("[Trader] Getting macro data...")
    macro = get_macro_environment()
    print(f"[Trader] Macro: {macro['environment']}")

    # Load scan results
    scan_results = load_scan_results()
    print(f"[Trader] Loaded {len(scan_results)} scan results")

    if not scan_results:
        print("[Trader] No scan results available — skipping")
        return

    # Get all tickers we need prices for
    all_tickers  = list(portfolio["positions"].keys())
    scan_tickers = [r["ticker"] for r in scan_results[:25]]
    all_tickers  = list(set(all_tickers + scan_tickers))

    print(f"[Trader] Fetching prices for {len(all_tickers)} tickers...")
    current_prices = get_prices(all_tickers)
    print(f"[Trader] Got {len(current_prices)} prices")

    # Update portfolio with current prices
    portfolio = update_position_prices(portfolio, current_prices)

    # Check stop losses first
    print("[Trader] Checking stop losses...")
    stops = check_stop_losses(portfolio, current_prices)
    for stop_msg in stops:
        print(f"  {stop_msg}")
        send_telegram(
            f"<code>STOP LOSS TRIGGERED</code>\n"
            f"<b>{stop_msg}</b>\n"
            f"<i>-7% limit hit — position closed automatically</i>"
        )

    # AI makes trading decisions
    print("[Trader] Asking AI for trading decisions...")
    actions = make_trading_decision(scan_results, portfolio, macro, current_prices)

    trades_made = []

    for action in actions:
        ticker     = action.get("ticker", "")
        trade_type = action.get("trade_type", "swing")
        reasoning  = action.get("reasoning", "")
        confidence = action.get("confidence", "MEDIUM")
        hold       = action.get("hold_duration", "—")
        target_pct = action.get("target_pct", 15)
        risk_note  = action.get("risk_note", "")

        if not ticker:
            continue

        if action["action"] == "BUY":
            price = current_prices.get(ticker)
            if not price:
                print(f"  [Trader] No price for {ticker} — skipping")
                continue

            # Skip if already in portfolio
            if ticker in portfolio["positions"]:
                print(f"  [Trader] Already holding {ticker} — skipping")
                continue

            # Calculate shares (max 10% of portfolio)
            max_cost = portfolio["total_value"] * 0.10
            shares   = int(max_cost / price)

            if shares == 0:
                print(f"  [Trader] {ticker} too expensive for 10% allocation")
                continue

            ok, msg = execute_buy(
                portfolio, ticker,
                next((r["name"] for r in scan_results if r["ticker"] == ticker), ticker),
                price, shares, trade_type, reasoning,
                next((r["score"] for r in scan_results if r["ticker"] == ticker), 0)
            )

            if ok:
                cost = price * shares
                print(f"  [Trader] BUY {ticker} — {shares} shares @ ${price} = ${cost:,.0f}")
                trades_made.append(action)

                alert = (
                    f"<code>TRADE  |  {datetime.utcnow().strftime('%d %b %Y  %H:%M UTC')}</code>\n"
                    f"<code>{'─'*35}</code>\n"
                    f"\n"
                    f"<b>BUY  |  ${ticker}</b>  [{confidence}]\n"
                    f"<code>Price      ${price:,.2f}</code>\n"
                    f"<code>Shares     {shares}</code>\n"
                    f"<code>Cost       ${cost:,.0f}</code>\n"
                    f"<code>Stop Loss  ${price*(1-0.07):,.2f}  (-7%)</code>\n"
                    f"<code>Target     +{target_pct}%</code>\n"
                    f"<code>Hold       {hold}</code>\n"
                    f"\n"
                    f"<b>AI Reasoning:</b>\n"
                    f"{reasoning}\n"
                    f"\n"
                    f"<code>Risk: {risk_note}</code>\n"
                    f"<i>Paper trade</i>"
                )
                send_telegram(alert)
            else:
                print(f"  [Trader] BUY {ticker} failed: {msg}")

        elif action["action"] == "SELL":
            if ticker not in portfolio["positions"]:
                print(f"  [Trader] {ticker} not in portfolio — skipping sell")
                continue

            price = current_prices.get(ticker) or portfolio["positions"][ticker]["current_price"]
            pos   = portfolio["positions"][ticker]
            ok, msg = execute_sell(portfolio, ticker, price, reasoning)

            if ok:
                pnl     = round((price - pos["entry_price"]) * pos["shares"], 2)
                pnl_pct = round((price - pos["entry_price"]) / pos["entry_price"] * 100, 2)
                print(f"  [Trader] SELL {ticker} @ ${price} | P&L: ${pnl:+,.0f} ({pnl_pct:+.1f}%)")
                trades_made.append(action)

                alert = (
                    f"<code>TRADE  |  {datetime.utcnow().strftime('%d %b %Y  %H:%M UTC')}</code>\n"
                    f"<code>{'─'*35}</code>\n"
                    f"\n"
                    f"<b>SELL  |  ${ticker}</b>\n"
                    f"<code>Price      ${price:,.2f}</code>\n"
                    f"<code>P&L        ${pnl:+,.0f}  ({pnl_pct:+.1f}%)</code>\n"
                    f"\n"
                    f"<b>AI Reasoning:</b>\n"
                    f"{reasoning}\n"
                    f"<i>Paper trade</i>"
                )
                send_telegram(alert)

    # Save updated portfolio
    save_portfolio(portfolio)
    print(f"[Trader] Portfolio saved: ${portfolio['total_value']:,.0f}")

    # Send portfolio update every run
    send_telegram(format_portfolio_update(portfolio))
    print(f"[Trader] Done. {len(trades_made)} trades made.")

if __name__ == "__main__":
    run_trader()
