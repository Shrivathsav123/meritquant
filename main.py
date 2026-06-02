#!/usr/bin/env python3
# main.py — Alpha Scanner — Your trading rules engine
import sys, os, json
from datetime import datetime
from universe import ALL_US, NSE_STOCKS, SECTOR_ETFS, US_STOCKS
from technical import analyze_ticker
from macro import get_macro_environment, format_macro_alert
from news import scan_news_for_ticker, get_related_etfs
from telegram_alerts import send, format_stock_alert, format_scan_summary, send_startup_message

DATA_DIR   = "data"
STORE_FILE = f"{DATA_DIR}/scan_results.json"
SENT_FILE  = f"{DATA_DIR}/sent_alerts.json"

os.makedirs(DATA_DIR, exist_ok=True)

MIN_SCORE = 3

def load_sent():
    try:
        return json.load(open(SENT_FILE))
    except:
        return {}

def save_sent(sent):
    json.dump(sent, open(SENT_FILE, "w"), indent=2)

def already_sent(ticker, score, sent):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    key   = f"{ticker}-{today}"
    return sent.get(key, 0) >= score

def mark_sent(ticker, score, sent):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    key   = f"{ticker}-{today}"
    sent[key] = score

def run_scan(tickers_dict, market="US", limit=None):
    results = []
    items   = list(tickers_dict.items())
    if limit:
        items = items[:limit]

    for ticker, name in items:
        try:
            result = analyze_ticker(ticker, name)
            news_score, news_signals = scan_news_for_ticker(ticker, name)
            result["score"]       += news_score
            result["news_signals"] = news_signals
            result["market"]       = market

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

            if result["score"] >= 5 and market == "US":
                result["related_etfs"] = get_related_etfs(ticker)

            results.append(result)

        except Exception as e:
            print(f"  Error on {ticker}: {e}")

    return results


def main():
    if "--startup" in sys.argv:
        send_startup_message()
        return

    dry_run = "--dry-run" in sys.argv
    print(f"\n[Alpha Scanner] Starting — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

    print("[Macro] Scanning environment...")
    macro = get_macro_environment()
    print(f"[Macro] Environment: {macro['environment']}")

    if macro.get("alerts"):
        send(format_macro_alert(macro))

    sent = load_sent()

    if "--etfs" in sys.argv:
        print("[Scanner] ETF scan mode...")
        results = run_scan(SECTOR_ETFS, market="ETF")

    elif "--nse" in sys.argv:
        print("[Scanner] NSE scan mode...")
        results = run_scan(dict(list(NSE_STOCKS.items())[:30]), market="NSE")

    elif "--us" in sys.argv:
        print("[Scanner] US stocks scan mode...")
        results = run_scan(dict(list(ALL_US.items())[:40]), market="US")

    else:
        print("[Scanner] Default scan (ETFs + top stocks)...")
        combined = {}
        combined.update(dict(list(SECTOR_ETFS.items())[:20]))
        combined.update(dict(list(US_STOCKS.items())[:10]))
        results = run_scan(combined, market="US")

    for r in results:
        r["score"] += macro["score_modifier"]
        r["score"]  = max(0, r["score"])

    results.sort(key=lambda x: x["score"], reverse=True)

    json.dump(
        [{k: v for k, v in r.items() if k != "rsi"} for r in results],
        open(STORE_FILE, "w"), indent=2, default=str
    )

    qualifying  = [r for r in results if r["score"] >= MIN_SCORE and r["conviction"] != "SKIP"]
    print(f"[Scanner] {len(qualifying)} qualifying signals (score >= {MIN_SCORE})")

    alerts_sent = 0
    for result in qualifying[:10]:
        ticker = result["ticker"]
        score  = result["score"]

        if already_sent(ticker, score, sent):
            print(f"  Already sent {ticker} today")
            continue

        if not dry_run:
            msg = format_stock_alert(
                result,
                news_signals=result.get("news_signals", []),
                macro_env=macro["environment"]
            )
            if send(msg):
                mark_sent(ticker, score, sent)
                alerts_sent += 1
        else:
            print(f"  Score breakdown: {ticker} = {result['score']} | RSI:{result.get('rsi',{})} | MA:{result.get('ma',{}).get('ma_signal','?')}")

            print(f"  [DRY RUN] Would send: {result['buy_rating']} {ticker} (score {score})")

    save_sent(sent)

    scan_count_file = f"{DATA_DIR}/scan_count.txt"
    try:
        count = int(open(scan_count_file).read()) + 1
    except:
        count = 1
    open(scan_count_file, "w").write(str(count))

    if count % 4 == 0 or len(qualifying) >= 5:
        send(format_scan_summary(results[:20], macro))

    print(f"[Scanner] Done. {alerts_sent} alerts sent.")


if __name__ == "__main__":
    main()
