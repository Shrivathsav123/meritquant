# trading/decision_engine.py — MeritQuant AI with memory, macro prediction, probabilistic entry

import json
import requests
import os
import re
from datetime import datetime
from memory import get_memory_context, record_lesson
from macro_calendar import get_macro_risk_score, get_upcoming_events_risk

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


def get_scan_context():
    import json, os
    from datetime import datetime, timezone
    path = 'data/alpha_terminal_scan.json'
    try:
        if not os.path.exists(path):
            return ""
        with open(path, 'r') as f:
            scan = json.load(f)
        scan_time = datetime.fromisoformat(scan.get('scan_time', '').replace('Z', '+00:00'))
        age_hours = (datetime.now(timezone.utc) - scan_time).total_seconds() / 3600
        if age_hours > 2:
            return f"[SCAN DATA STALE - {age_hours:.1f}hrs old, proceed with caution]"
        return f"""
=== ALPHA TERMINAL TECHNICAL SCAN ({scan['scan_type']} | {scan['scan_time']}) ===

MACRO REGIME: {scan['macro']['regime']}

VIX: {scan['macro']['vix']} | DXY: {scan['macro']['dxy']} | \nYield Spread (10Y-2Y): {scan['macro']['spread_10y_2y_bps']}bps

HYG: {scan['macro']['hyg']} | LQD: {scan['macro']['lqd']}

Position Size Cap: {scan['macro']['position_size_cap_pct']}% \n(${scan['macro']['position_size_cap_usd']:,.0f} max per trade)

Macro Notes: {scan['macro']['macro_notes']}

SECTOR ROTATION:

Leading (BUY SIDE): {scan['sector_rotation']['leading_sector']} \n  ({scan['sector_rotation']['leading_avg_pct']:+.2f}%)

Lagging (AVOID): {scan['sector_rotation']['lagging_sector']} \n  ({scan['sector_rotation']['lagging_avg_pct']:+.2f}%)

DXY Signal: {scan['sector_rotation']['dxy_equity_signal']}

Thesis: {scan['sector_rotation']['rotation_thesis']}

ACTIVE FVG SETUPS (ranked by conviction):

{chr(10).join([
    f"  #{i+1} {s['symbol']} {s['direction']} | "
    f"Entry: ${s['entry_price']} | Stop: ${s['stop_price']} | "
    f"Target: ${s['target_1']} | R:R {s['risk_reward']:.1f}x | "
    f"Gates: {s['gates_cleared']}/9 | Size: {s['position_size_pct']}% "
    f"(${s['position_size_usd']:,.0f}) | {s['thesis']}"
    for i, s in enumerate(scan.get('setups', []))
])}

WATCHLIST (not ready yet):

{chr(10).join([
    f"  {w['symbol']} - Alert at ${w['alert_price']} | Missing: {w['missing']}"
    for w in scan.get('no_trade_watchlist', [])
])}

SCANNER DIRECTIVE FOR THIS DECISION:

{scan['ai_directive']}

=== END SCAN DATA ===

"""
    except Exception as e:
        return f"[Scan data unavailable: {e}]"

SECTOR_UNIVERSE = {
    "AI_BOTTLENECK":  ["LITE","COHR","VRT","AMAT","LRCX","KLAC","VICR","MPWR","CDNS","SNPS","ANET","SMCI"],
    "BIOTECH":        ["MRNA","BNTX","REGN","VRTX","BIIB","GILD","AMGN","INCY","BMRN","ALNY"],
    "PHARMA":         ["LLY","NVO","PFE","JNJ","ABT","BMY","MRK","ABBV"],
    "OIL_GAS":        ["XOM","CVX","COP","EOG","MPC","VLO","HAL","SLB","OXY"],
    "ENERGY_INFRA":   ["CEG","VST","NRG","NEE","FSLR","ENPH","ETN","POWL"],
    "DEFENCE":        ["LMT","RTX","NOC","GD","BA","HII","KTOS","RKLB"],
    "MATERIALS":      ["FCX","SCCO","NEM","GOLD","AA","MP","APD","LIN"],
    "FINANCIALS":     ["JPM","GS","MS","BAC","BLK","V","MA","AXP","SCHW"],
    "HEALTHCARE":     ["UNH","CVS","HUM","CI","HCA","ISRG","BSX","DGX"],
    "CONSUMER":       ["AMZN","TSLA","NKE","SBUX","MCD","COST","HD","LOW"],
    "TELECOM_AI":     ["NOK","ERIC","INFN","CIEN","VIAV"],
}

QUANT_SYSTEM_PROMPT = """You are MeritQuant — an elite quantitative trading AI combining macro economics, probabilistic analysis, and deep market memory.

Your edge is NOT chart patterns alone. Your edge is the MARRIAGE of:
1. Macro regime identification (rates, credit, dollar, VIX)
2. Probability-weighted entry based on quantitative signals
3. News and catalyst analysis (jobs report, FOMC, earnings, geopolitics)
4. Memory of past mistakes — you NEVER repeat the same error
5. Technical patterns as CONFIRMATION only, never as the primary reason

═══════════════════════════════════════════
MACRO-FIRST FRAMEWORK
═══════════════════════════════════════════
Before ANY trade, answer these three questions:
1. What is the rate environment? (Rising rates = headwind for growth/tech)
2. What is the credit environment? (HYG falling = risk-off, avoid)
3. What is the dollar doing? (DXY rising = headwind for growth)

RATE HIKE PROBABILITY MODEL:
- Strong jobs report (NFP > 200k) = 40% probability of rate hike repricing → REDUCE tech exposure
- Hot CPI (>3.5%) = 60% rate hike probability → SHORT duration, favour energy/financials
- Weak jobs + falling CPI = rate cut probability → ADD growth/tech
- Fed hawkish language = 50% probability market sells off → trim positions
- Fed dovish pivot = 70% probability tech rallies → ADD growth

SECTOR ROTATION BY MACRO REGIME:
Rising rates + strong jobs:
→ FAVOUR: Energy (XOM, CVX), Financials (JPM, GS), Healthcare (UNH)
→ AVOID: High-multiple tech, growth, semis

Falling rates + weak dollar:
→ FAVOUR: AI semis (AMAT, NVDA, CDNS), Biotech (MRNA, REGN), Growth
→ AVOID: Banks, energy, commodities

Geopolitical risk + oil spike:
→ FAVOUR: Defence (LMT, RTX), Domestic energy (CEG, VST), Gold (NEM)
→ AVOID: Airlines, consumer discretionary, emerging markets

FDA/Biotech catalyst:
→ FAVOUR: mRNA platform (MRNA, BNTX), clinical stage (REGN, VRTX)
→ SIZE SMALLER: binary event risk

═══════════════════════════════════════════
PROBABILITY-WEIGHTED ENTRY SCORING
═══════════════════════════════════════════
Score trades on PROBABILITY not just pattern:

Macro alignment (+3 max):
+3 = macro regime PERFECTLY aligned (falling rates + oversold + catalyst)
+2 = macro neutral, technical strong
+1 = macro slight headwind but technical compelling
-2 = macro clearly against the trade
-3 = macro strongly against (rate hike fear, risk-off)

Catalyst quality (+3 max):
+3 = confirmed catalyst (signed contract, FDA approval, FOMC dovish)
+2 = high-probability catalyst (earnings beat track record, outbreak escalation)
+1 = speculative catalyst (rumour, analyst upgrade)
0 = no catalyst, pure technical

Technical confirmation (+2 max):
+2 = RSI oversold + moving average alignment + volume surge
+1 = one or two technical signals
0 = no technical confirmation

News flow (+2 max):
+2 = multiple bullish news sources, no negative noise
+1 = some bullish news
-1 = negative news in the sector
-2 = direct negative news about the company

MINIMUM SCORE TO BUY: 5+ (out of 10)
STRONG BUY: 7+ (out of 10)
DO NOT BUY: Below 5, or if macro score is -3

═══════════════════════════════════════════
MEMORY — YOU MUST READ THIS EVERY SCAN
═══════════════════════════════════════════
Memory of past mistakes is provided in the prompt.
You MUST reference it before every decision.
If memory says "do not hold semis into jobs reports" — DO NOT DO IT.
If memory says "oversold RSI bounce works in low VIX" — USE THAT.
Memory is your most powerful edge. Ignoring it is your biggest mistake.

═══════════════════════════════════════════
HOLD AND EXIT DISCIPLINE
═══════════════════════════════════════════
HOLD when:
- Position down 1-5% with thesis intact
- Market selloff caused by macro not company-specific news
- Within minimum hold period

SELL when:
- -7% hard stop (non-negotiable)
- Macro regime CHANGES against position (e.g. jobs report triggers rate hike fear)
- Company-specific negative news (earnings miss, FDA rejection, contract loss)
- Probability score drops below 3 after entry

NEVER sell because:
- Market had a bad day generally
- Position is uncomfortable
- Sector rotated briefly

═══════════════════════════════════════════
POSITION SIZING BY PROBABILITY
═══════════════════════════════════════════
Score 9-10: Full 10% position
Score 7-8: 7-8% position
Score 5-6: 5% position
Score below 5: DO NOT BUY
High binary risk (FDA, earnings): Max 5% regardless of score

Return ONLY valid JSON. No markdown. No preamble."""

def make_trading_decision(scan_results, portfolio, macro, current_prices):
    if not ANTHROPIC_API_KEY:
        print("[Trader] No API key — skipping")
        return []

    # Get memory context
    memory_context = get_memory_context()
    print(f"[Trader] Memory loaded: {len(memory_context)} chars")

    # Get quant macro risk
    macro_risk = get_macro_risk_score()
    upcoming   = get_upcoming_events_risk()
    print(f"[Trader] Macro risk: {macro_risk['regime']} (score {macro_risk['risk_score']}/10)")
    print(f"[Trader] Risk factors: {macro_risk['risk_factors'][:2]}")

    # Build opportunities
    opps = []
    for r in scan_results:
        if r.get("score", 0) >= 3:
            ticker  = r["ticker"]
            sector  = _get_sector(ticker)
            ma      = r.get("ma", {})
            rsi     = r.get("rsi", {})
            rsiVal  = rsi.get("daily", {}).get("rsi") if isinstance(rsi.get("daily"), dict) else None
            opps.append({
                "ticker":       ticker,
                "name":         r.get("name", ticker),
                "sector":       sector,
                "scan_score":   r.get("score", 0),
                "buy_rating":   r.get("buy_rating", ""),
                "price":        current_prices.get(ticker, 0),
                "rsi_daily":    rsiVal,
                "ma_signal":    ma.get("ma_signal", ""),
                "cross":        ma.get("cross", ""),
                "patterns":     r.get("patterns", [])[:3],
                "signals":      r.get("signals", [])[:3],
                "news":         [n["title"][:80] if isinstance(n, dict) else str(n)[:80]
                                 for n in r.get("news_signals", [])[:3]],
            })

    portfolio_summary = {
        "cash":      portfolio["cash"],
        "total":     portfolio["total_value"],
        "pnl_pct":   portfolio["pnl_pct"],
        "positions": {t: {
            "entry_price": p["entry_price"],
            "current_price": p.get("current_price", p["entry_price"]),
            "pnl_pct":     p.get("pnl_pct", 0),
            "days_held":   p.get("days_held", 0),
            "sector":      p.get("trade_type", ""),
            "reasoning":   p.get("reasoning", "")[:100],
            "stop_loss":   p.get("stop_loss", p["entry_price"] * 0.93),
        } for t, p in portfolio["positions"].items()},
    }

    max_pos = portfolio["total_value"] * 0.10

    prompt = f"""=== CURRENT DATE/TIME ===
{datetime.utcnow().strftime('%A %d %B %Y %H:%M UTC')}

=== YOUR MEMORY (read carefully — do not repeat past mistakes) ===
{memory_context}

=== QUANTITATIVE MACRO RISK ASSESSMENT ===
Regime: {macro_risk['regime']} (Risk Score: {macro_risk['risk_score']}/10)
Recommendation: {macro_risk['recommendation']}

Risk Factors:
{chr(10).join('- ' + r for r in macro_risk['risk_factors'])}

Bullish Factors:
{chr(10).join('- ' + b for b in macro_risk['bullish_factors'])}

Raw Data: {json.dumps(macro_risk['context'])}

Upcoming High-Impact Events:
{json.dumps(upcoming) if upcoming else 'None detected'}

=== PORTFOLIO ===
{json.dumps(portfolio_summary, indent=2)}

=== SCAN OPPORTUNITIES (score >= 3) ===
{json.dumps(opps[:25], indent=2)}

Max position: ${max_pos:,.0f} (10% of portfolio)

=== YOUR TASK ===
1. Read your memory — what mistakes have you made? What has worked?
2. Assess macro regime — is it safe to buy? What sectors fit?
3. Review open positions — apply exit discipline
4. Score new opportunities using the probability framework (0-10)
5. Only act on scores 5+

For each action, return:
[{{
  "action": "BUY" or "SELL" or "HOLD",
  "ticker": "AMAT",
  "sector": "AI_BOTTLENECK",
  "probability_score": 7,
  "macro_alignment": "Falling rates, VIX calm — semis favoured",
  "catalyst": "AI capex cycle, oversold after sector rotation",
  "reasoning": "Full reasoning — macro + catalyst + technical + memory reference",
  "sell_reason": "For SELL: why thesis broke, what macro changed",
  "lesson": "For SELL: what we learn from this trade",
  "position_size_pct": 7.0,
  "target_pct": 15.0,
  "stop_pct": 7.0,
  "hold_duration": "2-3 weeks",
  "confidence": "HIGH/MEDIUM/LOW"
}}]

Return [] if no high-probability setups or nothing to sell."""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-opus-4-8", "max_tokens": 2500, "system": get_scan_context() + "\n\n" + QUANT_SYSTEM_PROMPT, "messages": [{"role": "user", "content": prompt}]},
            timeout=50,
        )
        if resp.status_code != 200:
            print(f"[Trader] API error: {resp.status_code}")
            return []

        content = resp.json().get("content", [{}])[0].get("text", "[]")
        content = content.replace("```json", "").replace("```", "").strip()
        match   = re.search(r'\[[\s\S]*\]', content)
        if match:
            actions = json.loads(match.group(0))
            print(f"[Trader] AI decided {len(actions)} action(s)")
            for a in actions:
                prob = a.get("probability_score", "?")
                print(f"  {a.get('action')} {a.get('ticker')} [prob:{prob}/10] [{a.get('sector','')}]")
                print(f"    Macro: {a.get('macro_alignment','')[:80]}")
                print(f"    {a.get('reasoning','')[:100]}")

                # Record lessons from sells
                if a.get("action") == "SELL":
                    pos = portfolio["positions"].get(a.get("ticker", ""), {})
                    record_lesson(
                        ticker      = a.get("ticker", ""),
                        action      = "SELL",
                        pnl_pct     = pos.get("pnl_pct"),
                        reasoning   = a.get("reasoning", ""),
                        sell_reason = a.get("sell_reason", "") + " | " + a.get("lesson", ""),
                        macro_env   = macro_risk["regime"],
                        sector      = a.get("sector", ""),
                    )
            return actions
    except Exception as e:
        print(f"[Trader] Error: {e}")
    return []

def _get_sector(ticker):
    for sector, tickers in SECTOR_UNIVERSE.items():
        if ticker in tickers:
            return sector
    return "OTHER"
