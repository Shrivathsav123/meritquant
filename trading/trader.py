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
try:
    from trading.trade_reporter import send_trade_report
except Exception as e:
    print(f'Reporter import error: {e}')
    def send_trade_report(*a, **k): pass
try:
    from trading.excel_reporter import send_trade_excel
except Exception as e:
    print(f'Excel reporter error: {e}')
    def send_trade_excel(*a, **k): pass
try:
    from trading.excel_reporter import send_trade_excel
except Exception as e:
    print(f'Reporter import error: {e}')
    def send_trade_report(*a, **k): pass
try:
    from trading.excel_reporter import send_trade_excel
except Exception as e:
    print(f'Excel reporter error: {e}')
    def send_trade_excel(*a, **k): pass
try:
    from trading.memory import record_trade_outcome
except:
    def record_trade_outcome(*a, **k): pass
from macro import get_macro_environment

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

def log_trade(action, ticker, price, shares, reasoning, sell_reason=None, pnl=None, pnl_pct=None, sector=None, catalyst=None, risk_note=None):
    """Write every trade to a detailed trade log file."""
    os.makedirs("data", exist_ok=True)
    log_file = "data/trade_log.json"
    try:
        logs = json.load(open(log_file)) if os.path.exists(log_file) else []
    except:
        logs = []

    entry = {
        "date":        datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "action":      action,
        "ticker":      ticker,
        "price":       price,
        "shares":      shares,
        "cost":        round(price * shares, 2) if action == "BUY" else None,
        "pnl":         pnl,
        "pnl_pct":     pnl_pct,
        "sector":      sector or "UNKNOWN",
        "catalyst":    catalyst or "",
        "reasoning":   reasoning,
        "sell_reason": sell_reason or "",
        "risk_note":   risk_note or "",
    }

    logs.insert(0, entry)
    logs = logs[:200]  # Keep last 200 trades
    json.dump(logs, open(log_file, "w"), indent=2)
    print(f"[TradeLog] Logged {action} {ticker} @ ${price}")

def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(text); return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10,
        )
    except: pass

def get_price(ticker):
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
    except: pass
    return None

def get_prices(tickers):
    prices = {}
    for ticker in tickers:
        price = get_price(ticker)
        if price:
            prices[ticker] = price
    return prices

def load_scan_results():
    try:
        path = "data/scan_results.json"
        if os.path.exists(path):
            return json.load(open(path))
    except: pass
    return []

def format_portfolio_update(portfolio):
    now       = datetime.utcnow().strftime("%d %b %Y  %H:%M UTC")
    positions = portfolio["positions"]
    pnl       = portfolio["pnl"]
    pnl_pct   = portfolio["pnl_pct"]
    wins      = portfolio.get("wins", 0)
    losses    = portfolio.get("losses", 0)
    total     = wins + losses
    win_rate  = round(wins / total * 100) if total > 0 else 0

    # Build positions lines separately to avoid backslash in f-string
    pos_lines = ""
    for t, p in positions.items():
        pct   = p.get("pnl_pct", 0)
        price = p.get("current_price", 0)
        line  = "<code>  " + str(t).ljust(6) + "  $" + f"{price:>8.2f}" + "  " + f"{pct:>+6.1f}" + "%</code>"
        pos_lines += line + "\n"

    no_pos = "<code>  No open positions</code>\n"
    pos_block = pos_lines if pos_lines else no_pos

    msg  = f"<code>PORTFOLIO  |  {now}</code>\n"
    msg += "<code>" + "─"*35 + "</code>\n"
    msg += "\n"
    msg += f"<code>Total Value  ${portfolio['total_value']:>12,.0f}</code>\n"
    msg += f"<code>Cash         ${portfolio['cash']:>12,.0f}</code>\n"
    msg += f"<code>P&L          ${pnl:>+12,.0f}  ({pnl_pct:+.1f}%)</code>\n"
    msg += f"<code>Win Rate     {win_rate}%  ({wins}W / {losses}L)</code>\n"
    msg += "\n"
    msg += f"<b>Open Positions ({len(positions)}):</b>\n"
    msg += pos_block
    msg += "\n"
    msg += "<i>Paper trading — Not financial advice</i>"
    return msg

def is_nyse_open():
    """NYSE is open Mon-Fri 9:30AM - 4:00PM ET (UTC-4 in summer)."""
    from datetime import timezone, timedelta
    now_utc = datetime.now(timezone.utc)
    now_et  = now_utc + timedelta(hours=-4)  # EDT
    wd  = now_et.weekday()   # 0=Mon 6=Sun
    mins = now_et.hour * 60 + now_et.minute
    if wd >= 5:
        return False, "Weekend"
    if mins < 570:   # before 9:30 AM
        return False, f"Pre-market ({now_et.strftime('%H:%M')} ET)"
    if mins >= 960:  # after 4:00 PM
        return False, f"After-hours ({now_et.strftime('%H:%M')} ET)"
    return True, f"NYSE OPEN {now_et.strftime('%H:%M')} ET"

def run_trader():
    print(f"\n[Trader] Starting — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    open_status, open_msg = is_nyse_open()
    print(f"[Trader] Market: {open_msg}")
    if not open_status:
        print(f"[Trader] NYSE closed — updating prices only, no trades")
        try:
            portfolio = load_portfolio()
            if portfolio["positions"]:
                tickers = list(portfolio["positions"].keys())
                prices  = get_prices(tickers)
                portfolio = update_position_prices(portfolio, prices)
                save_portfolio(portfolio)
                print(f"[Trader] Prices updated: ${portfolio['total_value']:,.0f}")
        except Exception as e:
            print(f"[Trader] Price update error: {e}")
        return

    portfolio = load_portfolio()
    print(f"[Trader] Portfolio: ${portfolio['total_value']:,.0f} | Cash: ${portfolio['cash']:,.0f} | Positions: {len(portfolio['positions'])}")

    print("[Trader] Getting macro data...")
    macro = get_macro_environment()
    print(f"[Trader] Macro: {macro['environment']}")

    scan_results = load_scan_results()
    print(f"[Trader] Loaded {len(scan_results)} scan results")

    if not scan_results:
        print("[Trader] No scan results — skipping")
        return

    all_tickers  = list(portfolio["positions"].keys())
    scan_tickers = [r["ticker"] for r in scan_results[:25]]
    all_tickers  = list(set(all_tickers + scan_tickers))

    print(f"[Trader] Fetching prices for {len(all_tickers)} tickers...")
    current_prices = get_prices(all_tickers)
    print(f"[Trader] Got {len(current_prices)} prices")

    portfolio = update_position_prices(portfolio, current_prices)

    # Check stop losses
    stops = check_stop_losses(portfolio, current_prices)
    for stop_msg in stops:
        print(f"  {stop_msg}")
        send_telegram("<code>STOP LOSS TRIGGERED</code>\n<b>" + stop_msg + "</b>\n<i>-7% limit hit — closed automatically</i>")
        try:
            record_trade_outcome({"ticker": stop_msg.split(":")[1].strip().split(" ")[0] if ":" in stop_msg else "", "reasoning": "Stop loss triggered", "entry_price": 0, "shares": 0}, -7.0, 0, macro.get("environment","NEUTRAL"))
        except: pass

    # AI decisions
    print("[Trader] Asking AI for decisions...")
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

        if not ticker: continue

        if action["action"] == "BUY":
            price = current_prices.get(ticker)
            if not price:
                print(f"  No price for {ticker} — skipping"); continue

            if ticker in portfolio["positions"]:
                print(f"  Already holding {ticker} — skipping"); continue

            max_cost = portfolio["total_value"] * 0.10
            shares   = int(max_cost / price)
            if shares == 0:
                print(f"  {ticker} too expensive"); continue

            name = next((r["name"] for r in scan_results if r["ticker"] == ticker), ticker)
            score = next((r["score"] for r in scan_results if r["ticker"] == ticker), 0)
            ok, msg = execute_buy(portfolio, ticker, name, price, shares, trade_type, reasoning, score)

            if ok:
                cost = price * shares
                print(f"  BUY {ticker} — {shares} shares @ ${price} = ${cost:,.0f}")
                trades_made.append(action)
                send_trade_report(
                    action="BUY", ticker=ticker, price=price, shares=shares,
                    reasoning=reasoning,
                    sector=action.get("sector",""),
                    macro_env=action.get("macro_alignment","UNKNOWN"),
                    probability_score=action.get("probability_score",0),
                    macro_alignment=action.get("macro_alignment",""),
                    catalyst=action.get("catalyst",""),
                    risk_note=action.get("risk_note",""),
                    portfolio_value=portfolio["total_value"],
                    portfolio_pnl_pct=portfolio.get("pnl_pct",0),
                    target_pct=action.get("target_pct",15),
                    stop_pct=action.get("stop_pct",7),
                    hold_duration=action.get("hold_duration","2-3 weeks"),
                )
                stop_price = round(price * 0.93, 2)
                log_trade(
                    "BUY", ticker, price, shares,
                    reasoning  = reasoning,
                    sector     = action.get("sector",""),
                    catalyst   = action.get("catalyst",""),
                    risk_note  = action.get("risk_note",""),
                )
                alert  = f"<code>TRADE  |  {datetime.utcnow().strftime('%d %b %Y  %H:%M UTC')}</code>\n"
                alert += "<code>" + "─"*35 + "</code>\n\n"
                alert += f"<b>BUY  |  ${ticker}</b>  [{confidence}]\n"
                alert += f"<code>Price      ${price:,.2f}</code>\n"
                alert += f"<code>Shares     {shares}</code>\n"
                alert += f"<code>Cost       ${cost:,.0f}</code>\n"
                alert += f"<code>Stop Loss  ${stop_price:,.2f}  (-7%)</code>\n"
                alert += f"<code>Target     +{target_pct}%</code>\n"
                alert += f"<code>Hold       {hold}</code>\n\n"
                alert += f"<b>AI Reasoning:</b>\n{reasoning}\n\n"
                alert += f"<code>Risk: {risk_note}</code>\n"
                alert += "<i>Paper trade</i>"
                send_telegram(alert)
            else:
                print(f"  BUY {ticker} failed: {msg}")

        elif action["action"] == "SELL":
            if ticker not in portfolio["positions"]:
                print(f"  {ticker} not in portfolio — skipping"); continue

            pos   = portfolio["positions"][ticker]
            price = current_prices.get(ticker) or pos["current_price"]
            ok, msg = execute_sell(portfolio, ticker, price, reasoning)

            if ok:
                pnl     = round((price - pos["entry_price"]) * pos["shares"], 2)
                pnl_pct = round((price - pos["entry_price"]) / pos["entry_price"] * 100, 2)
                print(f"  SELL {ticker} @ ${price} | P&L: ${pnl:+,.0f} ({pnl_pct:+.1f}%)")
                send_trade_report(
                    action="SELL", ticker=ticker, price=price, shares=pos["shares"],
                    reasoning=action.get("reasoning",""),
                    sector=action.get("sector",""),
                    macro_env=action.get("macro_alignment","UNKNOWN"),
                    probability_score=action.get("probability_score",0),
                    macro_alignment=action.get("macro_alignment",""),
                    catalyst="",
                    risk_note="",
                    portfolio_value=portfolio["total_value"],
                    portfolio_pnl_pct=portfolio.get("pnl_pct",0),
                    sell_reason=action.get("sell_reason",""),
                    pnl=pnl, pnl_pct=pnl_pct,
                    lesson=action.get("lesson",""),
                )
                send_trade_excel(trigger_action='BUY', trigger_ticker=ticker)
                trades_made.append(action)
                # Record lesson for AI learning
                try:
                    from datetime import datetime as dt
                    entry_date = pos.get("entry_date", datetime.utcnow().isoformat())
                    held = max(1, (dt.utcnow() - dt.fromisoformat(entry_date.replace("Z",""))).days)
                    trade_record = {**pos, "sell_price": price, "ticker": ticker}
                    record_trade_outcome(trade_record, pnl_pct, held, macro.get("environment","NEUTRAL"))
                except Exception as me:
                    print(f"  Memory record error: {me}")
                log_trade(
                    "BUY", ticker, price, shares,
                    reasoning  = reasoning,
                    sector     = action.get("sector",""),
                    catalyst   = action.get("catalyst",""),
                    risk_note  = action.get("risk_note",""),
                )
                log_trade(
                    "SELL", ticker, price, pos["shares"],
                    reasoning  = reasoning,
                    sell_reason= action.get("sell_reason",""),
                    pnl        = pnl,
                    pnl_pct    = pnl_pct,
                    sector     = action.get("sector",""),
                )
                alert  = f"<code>TRADE  |  {datetime.utcnow().strftime('%d %b %Y  %H:%M UTC')}</code>\n"
                alert += "<code>" + "─"*35 + "</code>\n\n"
                alert += f"<b>SELL  |  ${ticker}</b>\n"
                alert += f"<code>Price      ${price:,.2f}</code>\n"
                alert += f"<code>P&L        ${pnl:+,.0f}  ({pnl_pct:+.1f}%)</code>\n\n"
                alert += f"<b>AI Reasoning:</b>\n{reasoning}\n"
                alert += "<i>Paper trade</i>"
                send_telegram(alert)

    save_portfolio(portfolio)
    print(f"[Trader] Portfolio saved: ${portfolio['total_value']:,.0f}")

    send_telegram(format_portfolio_update(portfolio))
    print(f"[Trader] Done. {len(trades_made)} trades made.")

if __name__ == "__main__":
    run_trader()
