#!/usr/bin/env python3
# main.py — Alpha Scanner
import sys, os, json
from datetime import datetime
from universe import ALL_US, NSE_STOCKS, SECTOR_ETFS, US_STOCKS
from technical import analyze_ticker
from macro import get_macro_environment, format_macro_alert
from news import get_news_for_scanner as scan_news_for_ticker
from telegram_alerts import send, format_stock_alert, format_scan_summary, send_startup_message

try:
    from reddit_scanner import run_reddit_scan
except Exception as e:
    print(f"Reddit import error: {e}")
    run_reddit_scan = None

try:
    from nse_scanner import run_nse_scan
except Exception as e:
    print(f"NSE import error: {e}")
    run_nse_scan = None

DATA_DIR   = "data"
STORE_FILE = f"{DATA_DIR}/scan_results.json"
SENT_FILE  = f"{DATA_DIR}/sent_alerts.json"
COUNT_FILE = f"{DATA_DIR}/scan_count.txt"

os.makedirs(DATA_DIR, exist_ok=True)

MIN_SCORE = 3

def load_sent():
    try: return json.load(open(SENT_FILE))
    except: return {}

def save_sent(sent):
    json.dump(sent, open(SENT_FILE, "w"), indent=2)

def already_sent(ticker, score, sent):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return sent.get(f"{ticker}-{today}", 0) >= score

def mark_sent(ticker, score, sent):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    sent[f"{ticker}-{today}"] = score

def get_count():
    try: return int(open(COUNT_FILE).read())
    except: return 0

def inc_count():
    n = get_count() + 1
    open(COUNT_FILE, "w").write(str(n))
    return n

def run_scan(tickers_dict, market="US"):
    results = []
    for ticker, name in tickers_dict.items():
        try:
            result = analyze_ticker(ticker, name)
            _news_result = scan_news_for_ticker(ticker, name)
            if isinstance(_news_result, tuple):
                news_signals, news_score = _news_result
            else:
                news_signals = _news_result
                news_score = 0
            result["score"]        += int(news_score or 0)
            result["news_signals"]  = news_signals if isinstance(news_signals, list) else []
            result["market"]        = market

            s = result["score"]
            if s >= 7:
                result["buy_rating"] = "STRONG BUY"
                result["conviction"] = "HIGH"
            elif s >= 5:
                result["buy_rating"] = "BUY"
                result["conviction"] = "MEDIUM"
            elif s >= 3:
                result["buy_rating"] = "WATCH"
                result["conviction"] = "LOW"
            else:
                result["buy_rating"] = "SKIP"
                result["conviction"] = "SKIP"

            if result["score"] >= 5:
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
    count   = inc_count()
    print(f"\n[Alpha Scanner] Scan #{count} — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

    # Macro
    print("[Macro] Scanning...")
    macro = get_macro_environment()
    print(f"[Macro] {macro['environment']}")
    if macro.get("alerts"):
        send(format_macro_alert(macro))

    sent = load_sent()

    # ── Scan scope ────────────────────────────────────────────
    # Rotate between ETFs and stocks each run
    # Odd scans = ETFs + top stocks, Even scans = more stocks
    if "--etfs" in sys.argv:
        print("[Scanner] ETF mode")
        combined = dict(list(SECTOR_ETFS.items())[:25])

    elif "--nse" in sys.argv:
        print("[Scanner] NSE mode")
        combined = dict(list(NSE_STOCKS.items())[:30])

    elif "--us" in sys.argv:
        print("[Scanner] US stocks mode")
        combined = dict(list(US_STOCKS.items())[:40])

    elif count % 2 == 0:
        # Even runs — focus on individual stocks
        print("[Scanner] Stock-focused scan")
        combined = {
            **dict(list(US_STOCKS.items())[:25]),  # Top 25 US stocks
            **dict(list(SECTOR_ETFS.items())[:5]),  # Just 5 key ETFs
        }
    else:
        # Odd runs — ETFs + stocks mixed
        print("[Scanner] Mixed ETF + stock scan")
        combined = {
            **dict(list(SECTOR_ETFS.items())[:15]),  # 15 ETFs
            **dict(list(US_STOCKS.items())[:15]),     # 15 stocks
        }

    print(f"[Scanner] Scanning {len(combined)} tickers...")
    results = run_scan(combined)

    # Apply macro
    for r in results:
        r["score"] += macro["score_modifier"]
        r["score"]  = max(0, r["score"])

    results.sort(key=lambda x: x["score"], reverse=True)

    # Save results to file (committed to repo by workflow)
    json.dump(
        [{k: v for k, v in r.items() if k not in ("rsi",)} for r in results],
        open(STORE_FILE, "w"), indent=2, default=str
    )

    # Also save to history
    hist_file = f"{DATA_DIR}/history.json"
    try:
        hist = json.load(open(hist_file)) if os.path.exists(hist_file) else []
    except:
        hist = []

    new_entries = [{
        **{k: v for k, v in r.items() if k not in ("rsi",)},
        "savedAt": datetime.utcnow().isoformat()
    } for r in results if r["score"] >= MIN_SCORE]

    hist = (new_entries + hist)[:500]
    json.dump(hist, open(hist_file, "w"), indent=2, default=str)

    # Send alerts
    qualifying  = [r for r in results if r["score"] >= MIN_SCORE and r["conviction"] != "SKIP"]
    print(f"[Scanner] {len(qualifying)} qualifying (score >= {MIN_SCORE})")

    alerts_sent = 0
    for result in qualifying[:8]:
        ticker = result["ticker"]
        score  = result["score"]

        if already_sent(ticker, score, sent):
            print(f"  Already sent {ticker}")
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
            print(f"  [DRY RUN] {result['buy_rating']} {ticker} score={score}")

    save_sent(sent)

    # Digest every 4th scan or 5+ signals
    if count % 4 == 0 or len(qualifying) >= 5:
        send(format_scan_summary(results[:30], macro))

    # Run global news scan — fetch from CNBC, Reuters, MarketWatch etc
    try:
        from news import scan_global_news, save_news_feed
        print("[News] Scanning CNBC, Reuters, MarketWatch, Yahoo Finance...")
        all_news = scan_global_news()
        save_news_feed(all_news)
    except Exception as e:
        print(f"[News] Global scan error: {e}")

    # Save live prices for app (including macro tickers)
    try:
        price_data = {}
        import requests
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        # Add macro tickers first
        macro_tickers = {
            "%5EVIX": "VIX", "DX-Y.NYB": "DXY", "TLT": "TLT",
            "%5ETNX": "TNX", "SPY": "SPY"
        }
        for raw_ticker, clean_name in macro_tickers.items():
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{raw_ticker}?range=1d&interval=1d"
                resp = requests.get(url, headers=headers, timeout=5)
                if resp.status_code == 200:
                    d = resp.json()
                    meta = d.get("chart",{}).get("result",[{}])[0].get("meta",{})
                    price = meta.get("regularMarketPrice") or meta.get("previousClose")
                    prev  = meta.get("previousClose") or meta.get("chartPreviousClose")
                    if price:
                        chg = ((float(price) - float(prev)) / float(prev) * 100) if prev else 0
                        price_data[clean_name] = {
                            "price":  round(float(price), 2),
                            "change": round(chg, 2),
                            "time":   datetime.utcnow().isoformat(),
                        }
            except: pass
        for r in results[:30]:
            ticker = r["ticker"]
            try:
                url  = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1d&interval=1m"
                resp = requests.get(url, headers=headers, timeout=5)
                if resp.status_code == 200:
                    data  = resp.json()
                    meta  = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
                    price = meta.get("regularMarketPrice") or meta.get("previousClose")
                    chg   = meta.get("regularMarketChangePercent", 0)
                    if price:
                        price_data[ticker] = {
                            "price":  round(float(price), 2),
                            "change": round(float(chg), 2),
                            "time":   datetime.utcnow().isoformat(),
                        }
            except: pass
        json.dump(price_data, open(f"{DATA_DIR}/prices.json", "w"), indent=2)
        print(f"[Scanner] Saved {len(price_data)} live prices")
    except Exception as e:
        print(f"[Scanner] Price save error: {e}")

    # Run Reddit scan every run
    try:
        if run_reddit_scan:
            print("[Reddit Scanner] Scanning WSB...")
            run_reddit_scan()
        else:
            print("[Reddit Scanner] Not available — skipping")
    except Exception as e:
        print(f"[Reddit Scanner] Non-fatal error: {e} — continuing")

    # Run NSE scan every other run
    try:
        if count % 2 == 0 and run_nse_scan:
            print("[NSE Scanner] Starting Indian market scan...")
            nse_results = run_nse_scan(send_telegram=send)
            print(f"[NSE Scanner] Done. {len(nse_results)} stocks scanned.")
    except Exception as e:
        print(f"[NSE Scanner] Non-fatal error: {e} — continuing")

    print(f"[Scanner] Done. {alerts_sent} alerts sent.")


if __name__ == "__main__":
    main()
