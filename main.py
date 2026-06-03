#!/usr/bin/env python3
# main.py — Alpha Scanner with bottleneck thesis
import sys, os, json
from datetime import datetime
from universe import ALL_US, NSE_STOCKS, SECTOR_ETFS, US_STOCKS, BOTTLENECK_STOCKS
from technical import analyze_ticker
from macro import get_macro_environment, format_macro_alert
from news import scan_news_for_ticker, get_related_etfs
from telegram_alerts import send, format_stock_alert, format_scan_summary, send_startup_message

DATA_DIR   = "data"
STORE_FILE = f"{DATA_DIR}/scan_results.json"
SENT_FILE  = f"{DATA_DIR}/sent_alerts.json"
COUNT_FILE = f"{DATA_DIR}/scan_count.txt"

os.makedirs(DATA_DIR, exist_ok=True)
MIN_SCORE = 3

# ── Priority — scanned every single run ──────────────────────
PRIORITY_STOCKS = {
    # Mag 7
    "AAPL":  "Apple Inc",
    "MSFT":  "Microsoft Corporation",
    "GOOGL": "Alphabet (Google)",
    "AMZN":  "Amazon",
    "META":  "Meta Platforms",
    "TSLA":  "Tesla Inc",
    "NVDA":  "NVIDIA Corporation",
    # Semis
    "AMD":   "Advanced Micro Devices",
    "MU":    "Micron Technology",
    "SNDK":  "SanDisk Corp",
    "INTC":  "Intel Corporation",
    "QCOM":  "Qualcomm",
    "AVGO":  "Broadcom",
    "TSM":   "Taiwan Semiconductor",
    "ARM":   "ARM Holdings",
    # Top bottlenecks — always watch these
    "LITE":  "Lumentum (photonics)",
    "COHR":  "Coherent (optical)",
    "VRT":   "Vertiv (cooling)",
    "ANET":  "Arista Networks",
    "MPWR":  "Monolithic Power",
    "VICR":  "Vicor Corp",
    "CDNS":  "Cadence Design",
    "SNPS":  "Synopsys",
    # Key ETFs
    "SMH":   "VanEck Semiconductor ETF",
    "QQQ":   "Invesco QQQ",
    "SPY":   "SPDR S&P 500",
    "XLE":   "Energy Select SPDR",
    "XOP":   "SPDR Oil & Gas",
}

# Bottleneck rotation groups — scan in batches
BOTTLENECK_GROUPS = {
    "photonics":  ["LITE", "COHR", "MTSI", "FN", "VIAV", "INFN", "CIEN"],
    "power":      ["VRT", "MOD", "ETN", "POWL", "CEG", "VST", "VICR", "MPWR"],
    "packaging":  ["AMAT", "LRCX", "KLAC", "ONTO", "ACMR"],
    "networking": ["ANET", "MRVL", "APH", "TTMI"],
    "materials":  ["FCX", "SCCO", "APD", "LIN", "CCJ", "UUUU"],
    "design":     ["CDNS", "SNPS", "SMCI"],
    "defence_ai": ["KTOS", "RKLB", "ACHR"],
}

def load_sent():
    try: return json.load(open(SENT_FILE))
    except: return {}

def save_sent(sent): json.dump(sent, open(SENT_FILE, "w"), indent=2)

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
            news_score, news_signals = scan_news_for_ticker(ticker, name)
            result["score"]        += news_score
            result["news_signals"]  = news_signals
            result["market"]        = market

            s = result["score"]
            if s >= 7:   result["buy_rating"] = "STRONG BUY"; result["conviction"] = "HIGH"
            elif s >= 5: result["buy_rating"] = "BUY";        result["conviction"] = "MEDIUM"
            elif s >= 3: result["buy_rating"] = "WATCH";      result["conviction"] = "LOW"
            else:        result["buy_rating"] = "SKIP";       result["conviction"] = "SKIP"

            if result["score"] >= 5:
                result["related_etfs"] = get_related_etfs(ticker)

            results.append(result)
        except Exception as e:
            print(f"  Error on {ticker}: {e}")
    return results


def main():
    if "--startup" in sys.argv:
        send_startup_message(); return

    dry_run = "--dry-run" in sys.argv
    count   = inc_count()
    print(f"\n[Alpha Scanner] Scan #{count} — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

    print("[Macro] Scanning...")
    macro = get_macro_environment()
    print(f"[Macro] {macro['environment']}")
    if macro.get("alerts"):
        send(format_macro_alert(macro))

    sent = load_sent()

    # ── Build scan list ───────────────────────────────────────
    combined = dict(PRIORITY_STOCKS)  # Always start with priority

    if "--nse" in sys.argv:
        combined = dict(list(NSE_STOCKS.items())[:30])

    elif "--us" in sys.argv:
        combined.update(dict(list(US_STOCKS.items())[:20]))

    elif "--etfs" in sys.argv:
        combined.update(dict(list(SECTOR_ETFS.items())[:20]))

    elif "--bottlenecks" in sys.argv:
        # Scan all bottleneck stocks
        combined.update(BOTTLENECK_STOCKS)

    else:
        # Rotate bottleneck groups each scan
        # Scan 1=photonics, 2=power, 3=packaging, 4=networking, 5=materials...
        groups = list(BOTTLENECK_GROUPS.keys())
        group_name = groups[(count - 1) % len(groups)]
        group_tickers = BOTTLENECK_GROUPS[group_name]
        group_dict = {t: BOTTLENECK_STOCKS.get(t, t) for t in group_tickers}

        print(f"[Scanner] Bottleneck rotation: {group_name.upper()}")
        combined.update(group_dict)

        # Add some regular stocks too
        if count % 2 == 0:
            combined.update(dict(list(US_STOCKS.items())[:10]))
        else:
            combined.update(dict(list(SECTOR_ETFS.items())[:8]))

    print(f"[Scanner] Scanning {len(combined)} tickers...")
    results = run_scan(combined)

    for r in results:
        r["score"] += macro["score_modifier"]
        r["score"]  = max(0, r["score"])

    results.sort(key=lambda x: x["score"], reverse=True)

    json.dump(
        [{k: v for k, v in r.items() if k not in ("rsi",)} for r in results],
        open(STORE_FILE, "w"), indent=2, default=str
    )

    hist_file = f"{DATA_DIR}/history.json"
    try: hist = json.load(open(hist_file)) if os.path.exists(hist_file) else []
    except: hist = []

    new_entries = [{
        **{k: v for k, v in r.items() if k not in ("rsi",)},
        "savedAt": datetime.utcnow().isoformat()
    } for r in results if r["score"] >= MIN_SCORE]

    json.dump((new_entries + hist)[:500], open(hist_file, "w"), indent=2, default=str)

    qualifying  = [r for r in results if r["score"] >= MIN_SCORE and r["conviction"] != "SKIP"]
    print(f"[Scanner] {len(qualifying)} qualifying")

    alerts_sent = 0
    for result in qualifying[:8]:
        ticker = result["ticker"]
        score  = result["score"]
        if already_sent(ticker, score, sent): continue
        if not dry_run:
            msg = format_stock_alert(result, news_signals=result.get("news_signals",[]), macro_env=macro["environment"])
            if send(msg):
                mark_sent(ticker, score, sent)
                alerts_sent += 1
        else:
            print(f"  [DRY RUN] {result['buy_rating']} {ticker} score={score}")

    save_sent(sent)
    if count % 4 == 0 or len(qualifying) >= 5:
        send(format_scan_summary(results[:30], macro))

    print(f"[Scanner] Done. {alerts_sent} alerts sent.")

if __name__ == "__main__":
    main()
