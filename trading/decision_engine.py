# trading/decision_engine.py
# AI decision engine with bottleneck thesis bias

import json
import requests
import os
from datetime import datetime

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Derived demand map — when X is running, buy these bottlenecks
DERIVED_DEMAND = {
    "NVDA": ["LITE", "COHR", "VRT", "AMAT", "VICR", "MPWR", "CDNS"],
    "AMD":  ["AMAT", "LRCX", "KLAC", "VRT", "MPWR"],
    "MSFT": ["ANET", "VRT", "ETN", "CDNS"],
    "GOOGL":["ANET", "VRT", "APD", "LIN", "COHR"],
    "AMZN": ["ANET", "VRT", "ETN", "APH", "COHR"],
    "META": ["ANET", "COHR", "VRT", "LITE"],
    "TSLA": ["VICR", "MPWR", "FCX", "SCCO"],
    "TSM":  ["AMAT", "LRCX", "KLAC", "APD", "LIN"],
    "MU":   ["AMAT", "LRCX", "KLAC", "ONTO"],
    "SNDK": ["AMAT", "LRCX", "ONTO"],
    "AVGO": ["COHR", "LITE", "APH", "TTMI"],
    "MRVL": ["COHR", "LITE", "APH"],
    "SMH":  ["LITE", "COHR", "VRT", "AMAT", "LRCX", "VICR"],
    "SOXX": ["LITE", "COHR", "AMAT", "LRCX", "MPWR"],
}

BOTTLENECK_TICKERS = {
    "LITE", "COHR", "MTSI", "FN", "VIAV", "INFN", "CIEN",
    "AMAT", "LRCX", "KLAC", "ONTO", "ACMR",
    "VICR", "MPWR", "VRT", "MOD", "ETN", "POWL",
    "CEG", "VST", "FCX", "SCCO", "APD", "LIN",
    "ANET", "MRVL", "APH", "TTMI",
    "CDNS", "SNPS", "SMCI", "CCJ", "KTOS",
}

TRADER_SYSTEM_PROMPT = """You are an elite quantitative trader managing a $183,000 paper trading portfolio.

YOUR CORE THESIS — BOTTLENECK INVESTING:
The biggest gains come from owning the supply chain CONSTRAINTS, not the end product.
NVDA is already up 10x — but the companies that SUPPLY NVDA are still early.
Examples:
- Chips need photonics to transmit data → LITE, COHR (optical transceivers)
- AI data centres need power → VRT (cooling), ETN (power), POWL (transformers)
- Every chip starts as software design → CDNS, SNPS (EDA monopolies, literal monopolies)
- Chips need specialty gases to manufacture → APD, LIN
- AI needs copper wiring everywhere → FCX, SCCO
- Nuclear power is being revived for AI data centres → CEG, VST, CCJ

DERIVED DEMAND RULE — THIS IS YOUR PRIMARY EDGE:
When a primary tech stock (NVDA, AMD, MSFT etc) is strong/running:
→ BUY the bottleneck stocks that supply it, NOT the primary stock itself
→ The primary stock already ran — the suppliers are lagging and about to catch up
→ This is how smart money front-runs the next leg

YOUR TRADING RULES:
1. RSI oversold (30-50) across multiple timeframes = strong entry signal
2. Golden Cross (50MA > 200MA) = bullish momentum confirmed
3. 200MA as key support — buying near 200MA is low risk entry
4. Fibonacci 61.8% = golden ratio entry, strongest level
5. Breakout with volume = institutional buying confirmed
6. News catalyst (Trump, government contract, earnings beat) = override technicals
7. Macro: VIX low = risk on, deploy more. DXY falling = bullish for stocks + NSE.
8. Bullish RSI divergence = momentum shifting before price moves

PORTFOLIO RULES:
- Max 10% per position
- Hard -7% stop loss on every trade
- Prefer bottleneck stocks over mega caps
- Mega caps (AAPL, MSFT, GOOGL, AMZN) only if RSI strongly oversold + clear catalyst
- Goal: aggressive growth — target 50-100% portfolio return
- Hold winners longer — if thesis intact, don't sell early
- Cut losers fast — if support breaks, exit

POSITION SIZING BIAS:
- Bottleneck stock with strong setup = full 10% allocation
- Mega cap with strong setup = max 7% (they move slower)
- Speculative bottleneck = 5% only

You must respond ONLY with valid JSON. No markdown, no explanation outside JSON."""

def get_derived_demand_plays(scan_results):
    """
    Find bottleneck stocks to buy based on what primary stocks are running.
    Returns list of derived demand opportunities.
    """
    derived = []
    strong_primaries = [r["ticker"] for r in scan_results if r.get("score", 0) >= 5]

    for primary in strong_primaries:
        if primary in DERIVED_DEMAND:
            for bottleneck in DERIVED_DEMAND[primary]:
                # Check if bottleneck is in scan results
                bn_result = next((r for r in scan_results if r["ticker"] == bottleneck), None)
                if bn_result:
                    derived.append({
                        "ticker":         bottleneck,
                        "primary_driver": primary,
                        "score":          bn_result.get("score", 0),
                        "derived_reason": f"{primary} is strong — {bottleneck} supplies it directly",
                    })

    return derived

def make_trading_decision(scan_results, portfolio, macro, current_prices):
    if not ANTHROPIC_API_KEY:
        print("[Trader] No ANTHROPIC_API_KEY — skipping")
        return []

    # Get derived demand plays
    derived_plays = get_derived_demand_plays(scan_results)
    print(f"[Trader] Found {len(derived_plays)} derived demand plays")

    # Build opportunities — bottleneck stocks first
    opportunities = []

    # Add bottleneck stocks first (priority)
    for r in scan_results:
        if r["ticker"] in BOTTLENECK_TICKERS and r.get("score", 0) >= 3:
            ticker = r["ticker"]
            opportunities.append({
                "ticker":       ticker,
                "name":         r.get("name", ticker),
                "score":        r.get("score", 0),
                "rating":       r.get("buy_rating", ""),
                "price":        current_prices.get(ticker, 0),
                "category":     "BOTTLENECK",
                "ma_signal":    r.get("ma", {}).get("ma_signal", ""),
                "cross":        r.get("ma", {}).get("cross", ""),
                "patterns":     r.get("patterns", [])[:3],
                "signals":      r.get("signals", [])[:3],
                "news":         [n["title"][:80] for n in r.get("news_signals", [])[:2]],
                "rsi_daily":    r.get("rsi", {}).get("daily", {}).get("rsi"),
                "rsi_weekly":   r.get("rsi", {}).get("weekly", {}).get("rsi"),
            })

    # Add derived demand plays
    for d in derived_plays[:5]:
        ticker = d["ticker"]
        existing = next((o for o in opportunities if o["ticker"] == ticker), None)
        if existing:
            existing["derived_demand"] = d["derived_reason"]
            existing["primary_driver"] = d["primary_driver"]
        else:
            opportunities.append({
                "ticker":         ticker,
                "name":           ticker,
                "score":          d["score"],
                "price":          current_prices.get(ticker, 0),
                "category":       "DERIVED_DEMAND",
                "derived_demand": d["derived_reason"],
                "primary_driver": d["primary_driver"],
            })

    # Add non-bottleneck stocks at end (lower priority)
    for r in scan_results:
        if r["ticker"] not in BOTTLENECK_TICKERS and r.get("score", 0) >= 5:
            ticker = r["ticker"]
            if not any(o["ticker"] == ticker for o in opportunities):
                opportunities.append({
                    "ticker":    ticker,
                    "name":      r.get("name", ticker),
                    "score":     r.get("score", 0),
                    "rating":    r.get("buy_rating", ""),
                    "price":     current_prices.get(ticker, 0),
                    "category":  "LARGE_CAP",
                    "ma_signal": r.get("ma", {}).get("ma_signal", ""),
                    "cross":     r.get("ma", {}).get("cross", ""),
                    "signals":   r.get("signals", [])[:3],
                    "rsi_daily": r.get("rsi", {}).get("daily", {}).get("rsi"),
                })

    # Current positions for sell analysis
    portfolio_summary = {
        "cash":        portfolio["cash"],
        "total_value": portfolio["total_value"],
        "pnl":         portfolio["pnl"],
        "pnl_pct":     portfolio["pnl_pct"],
        "positions": {t: {
            "shares":        p["shares"],
            "entry_price":   p["entry_price"],
            "current_price": p.get("current_price", p["entry_price"]),
            "pnl_pct":       p.get("pnl_pct", 0),
            "trade_type":    p["trade_type"],
            "reasoning":     p["reasoning"][:100],
        } for t, p in portfolio["positions"].items()},
    }

    macro_summary = {
        "environment": macro.get("environment", "NEUTRAL"),
        "vix":         macro.get("vix", {}).get("value", "N/A"),
        "dxy":         macro.get("dxy", {}).get("value", "N/A"),
        "yield_10yr":  macro.get("bonds", {}).get("yield_10yr", "N/A"),
        "tlt_change":  macro.get("bond_etfs", {}).get("TLT", {}).get("change", 0),
        "alerts":      macro.get("alerts", []),
    }

    max_position = portfolio["total_value"] * 0.10

    prompt = f"""Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}

PORTFOLIO:
{json.dumps(portfolio_summary, indent=2)}

MACRO:
{json.dumps(macro_summary, indent=2)}

OPPORTUNITIES (bottleneck stocks listed first):
{json.dumps(opportunities[:25], indent=2)}

Max position size: ${max_position:,.0f} (10% of portfolio)

INSTRUCTIONS:
1. First check if any open positions need to be SOLD (take profit, thesis broken, better opportunity)
2. Look for BOTTLENECK and DERIVED_DEMAND category stocks first — these are your primary targets
3. Only buy LARGE_CAP stocks if RSI is strongly oversold AND there is a clear catalyst
4. Remember: derived demand plays (bottlenecks) move AFTER the primary stock — get in before they catch up
5. Max 3 new buys per scan

Return JSON array of actions:
[
  {{
    "action": "BUY" or "SELL",
    "ticker": "VICR",
    "trade_type": "swing",
    "reasoning": "2-3 sentences — mention the bottleneck thesis and specific technical setup",
    "confidence": "HIGH" or "MEDIUM" or "LOW",
    "hold_duration": "1-2 weeks",
    "target_pct": 15.0,
    "risk_note": "What would invalidate this trade"
  }}
]

Return [] if no strong setups. Quality over quantity."""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      "claude-sonnet-4-20250514",
                "max_tokens": 1500,
                "system":     TRADER_SYSTEM_PROMPT,
                "messages":   [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )

        if resp.status_code != 200:
            print(f"[Trader] API error: {resp.status_code} — {resp.text[:100]}")
            return []

        content = resp.json().get("content", [{}])[0].get("text", "[]")
        content = content.replace("```json", "").replace("```", "").strip()

        import re
        match = re.search(r'\[[\s\S]*\]', content)
        if match:
            actions = json.loads(match.group(0))
            print(f"[Trader] AI decided {len(actions)} action(s)")
            for a in actions:
                print(f"  {a.get('action')} {a.get('ticker')} [{a.get('confidence')}] — {a.get('reasoning','')[:80]}")
            return actions

    except Exception as e:
        print(f"[Trader] Decision error: {e}")

    return []
