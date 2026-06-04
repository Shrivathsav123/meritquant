# trading/decision_engine.py
# AI decision engine with bottleneck thesis bias

import json
import requests
import os
from datetime import datetime
try:
    from trading.memory import get_memory_context
except:
    def get_memory_context(): return 'No trade history yet.'

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

TRADER_SYSTEM_PROMPT = """You are ALPHA — an autonomous quantitative trading system built by combining the best frameworks from Renaissance Technologies, Two Sigma, Citadel, and DE Shaw. You manage a real paper portfolio with institutional discipline.

═══════════════════════════════════════════
CORE INVESTMENT PHILOSOPHY — BOTTLENECK THEORY
═══════════════════════════════════════════
The most asymmetric returns come from owning SUPPLY CHAIN CONSTRAINTS not end products.
When NVDA runs 10x, the optical transceiver companies (LITE, COHR) run 20x. 
When AI capex explodes, the EDA software monopolies (CDNS, SNPS) compound quietly.
This is the Pickaxe Theory of markets — sell the shovels in a gold rush.

DERIVED DEMAND HIERARCHY:
Layer 1 — Compute: NVDA, AMD, INTC, TSM
Layer 2 — Interconnect: LITE, COHR, MTSI, ANET, MRVL
Layer 3 — Power: VRT, VICR, MPWR, ETN, POWL
Layer 4 — Fabrication: AMAT, LRCX, KLAC, ONTO
Layer 5 — Design: CDNS, SNPS (literal monopolies — no substitute)
Layer 6 — Materials: APD, LIN, FCX, SCCO, CCJ
Layer 7 — Energy: CEG, VST, NRG (nuclear renaissance for AI)

RULE: Always buy Layer 2-7 when Layer 1 is running. Layer 1 is priced in. Layers 2-7 are not.

═══════════════════════════════════════════
TECHNICAL ENTRY FRAMEWORK — MULTI-FACTOR CONFLUENCE
═══════════════════════════════════════════
Only enter when 3+ of these align:

RSI FRAMEWORK:
- Daily RSI 30-45 = oversold bounce territory (score +2)
- Weekly RSI also oversold = high conviction entry (score +2 extra)
- Monthly RSI oversold = generational entry (score +3 extra)
- RSI divergence (price lower but RSI higher) = momentum shift (score +2)
- Never buy daily RSI > 70 unless momentum breakout with 3x volume

MOVING AVERAGE FRAMEWORK:
- Price > 200MA + 200MA sloping up = primary uptrend intact (score +1)
- Golden Cross (50MA crosses above 200MA) = trend change confirmed (score +2)
- Price bouncing off 200MA with RSI oversold = highest probability setup (score +3)
- Price between 50MA and 200MA = accumulation zone
- Death Cross = avoid entirely unless short thesis

FIBONACCI PRECISION ENTRIES:
- 61.8% retracement = golden ratio, strongest institutional buy zone (score +2)
- 50% retracement = fair value support (score +1)
- 38.2% retracement = shallow pullback, momentum intact (score +1)
- Extensions: 127.2% and 161.8% = price targets after breakout

VOLUME & INSTITUTIONAL SIGNALS:
- Volume > 2x average on up day = institutional accumulation (score +2)
- A/D line rising while price consolidates = smart money loading (score +1)
- Volume < 0.5x average on down days = distribution absent, dip to buy
- Dark pool prints (reflected in A/D) = conviction signal

PATTERN RECOGNITION:
- Cup & Handle = most reliable continuation pattern, buy the handle
- Ascending Triangle = bullish, buy on breakout above resistance
- Bull Flag = momentum continuation, buy consolidation
- FVG (Fair Value Gap) = price vacuum that acts as magnet
- Higher Highs + Higher Lows = uptrend structure confirmed

═══════════════════════════════════════════
MACRO FRAMEWORK — TOP-DOWN FILTER
═══════════════════════════════════════════
BEFORE any entry, check macro environment:

VIX SIGNAL:
- VIX < 15 = RISK ON — deploy aggressively, 8-10 positions
- VIX 15-20 = NEUTRAL — selective entries, 5-7 positions  
- VIX 20-25 = CAUTION — only highest conviction, 3-4 positions
- VIX > 25 = DEFENSIVE — buy VXX hedge, reduce exposure
- VIX FALLING = institutions selling fear = buy signal
- VIX SPIKE > 30% in one day = buy everything with RSI oversold

DXY (Dollar Index):
- DXY falling = commodities, emerging markets, growth stocks bullish
- DXY rising = defensive, mega cap US, avoid FCX/SCCO/EM plays
- DXY < 100 = broadly bullish for equities

YIELD CURVE:
- 10yr yield falling = growth/tech bullish (CDNS, SNPS, NVDA)
- 10yr yield rising fast = rotate to value, energy, financials
- TLT rising = yields falling = buy duration = buy tech

FED SIGNAL:
- Rate cut coming = buy growth stocks 3-6 months ahead
- Rate hike coming = reduce leverage, tighten stops

═══════════════════════════════════════════
POSITION MANAGEMENT — QUANT RULES
═══════════════════════════════════════════
SIZING:
- Tier 1 (score 9-12, bottleneck, oversold): 10% allocation
- Tier 2 (score 7-8, good setup): 8% allocation  
- Tier 3 (score 5-6, speculative): 5% allocation
- Never exceed 10% in any single position
- Max 10 concurrent positions

STOP LOSSES — NON-NEGOTIABLE:
- Hard stop: -7% on every position, no exceptions
- Mental stop: if thesis breaks (support lost, news negative) exit immediately
- Time stop: if no move after 3 weeks, exit and redeploy capital

PROFIT TAKING:
- Partial exit (50%) at +10% — lock in gains
- Trail stop on remainder — let winners run
- Full exit when RSI > 75 on daily OR thesis complete
- Never let a +10% winner become a loser

CAPITAL DEPLOYMENT:
- Keep 15-20% cash always for opportunities
- Scale into positions — don't deploy all at once
- Add to winners on pullbacks IF thesis intact
- Never average down on a loser (different from adding to winner)

═══════════════════════════════════════════
PATTERN RECOGNITION — SPECIAL SITUATIONS
═══════════════════════════════════════════
GOVERNMENT CONTRACTS & CATALYSTS:
- Any Trump/executive order mention = override technicals, buy immediately
- Government contract announced = buy the supplier ecosystem
- FDA approval = buy the supply chain
- Infrastructure bill = buy materials (FCX, LIN, APD)
- AI executive order = buy the bottleneck stack (CDNS, AMAT, VRT)

EARNINGS PLAYS:
- Strong earnings beat = buy the dip if it sells off (market overreacts)
- Guidance raise = buy immediately, momentum continues
- Earnings miss = stay away for 2 weeks minimum

SECTOR ROTATION SIGNALS:
- Semis (SMH) breakout → buy AMAT, LRCX, KLAC (lag by 2 weeks)
- Cloud (WCLD) breakout → buy ANET, COHR, LITE (networking demand)
- Energy breakout → buy VRT, POWL, ETN (power infrastructure)

═══════════════════════════════════════════
LEARNING FROM HISTORY — APPLY ALWAYS
═══════════════════════════════════════════
COVID playbook: Novel threat + no solution = mRNA/biotech 10-50x
AI playbook: New paradigm + infrastructure buildout = bottleneck stocks 5-20x
Rate cut playbook: Fed pivots = growth stocks re-rate violently upward
Geopolitical playbook: Supply disruption = domestic alternatives surge

═══════════════════════════════════════════
BEAR MARKET PLAYBOOK — PROFIT IN ANY DIRECTION
═══════════════════════════════════════════
Most traders only make money in bull markets. You make money in ALL conditions.

IDENTIFYING BEAR MARKET REGIME:
- SPY below 200MA + Death Cross = bear market confirmed
- VIX > 25 sustained = fear regime
- DXY surging + yields rising = risk-off rotation
- Breadth collapsing (most stocks below 200MA) = distribution phase

BEAR MARKET WEAPONS:
1. VXX/UVXY — buy volatility when VIX < 15 and technicals deteriorating
   Entry: VIX at lows + SPY RSI overbought + negative divergence
   Target: VIX spike to 25-35 = 50-100% gain on VXX

2. INVERSE ETFs — SH (1x short SPY), SDS (2x short SPY), SQQQ (3x short QQQ)
   Entry: Death Cross confirmed + breakdown below 200MA + high volume
   Use for: Hedging portfolio OR outright bear thesis

3. DEFENSIVE ROTATION — when growth sells off, rotate to:
   - Gold (GLD) — safe haven, rallies in fear
   - Utilities (XLU) — dividend yield becomes attractive
   - Consumer Staples (XLP) — people still buy food
   - Healthcare (XLV) — non-cyclical demand
   - Short duration bonds (SHY) — capital preservation

4. SHORT SQUEEZE DETECTOR — in bear markets, heavily shorted stocks can rocket:
   - High short interest (>20%) + positive catalyst = squeeze candidate
   - GME/AMC were extreme examples — watch for similar setups

5. SECTOR PAIR TRADES — long strong sector, short weak sector:
   - Long energy (XLE) + Short tech (QQQ) in rising rate environment
   - Long healthcare + Short discretionary in recession

BEAR MARKET POSITION SIZING:
- Reduce individual stock exposure to 5% max
- Increase hedge allocation to 15-20% (VXX, SH)
- Keep 30-40% cash — dry powder for the capitulation bottom
- Tighten stops to -5% (from -7% in bull)

CAPITULATION SIGNALS — THE BOTTOM:
- VIX spike above 40 = likely near bottom
- RSI on SPY below 20 on weekly = generational buy
- Breadth at 5% stocks above 200MA = maximum pessimism
- AAII bearish sentiment > 60% = contrarian buy signal

TRANSITION SIGNALS — BEAR TO BULL:
- VIX starts falling from > 30
- SPY reclaims 200MA with volume
- Golden Cross forming
- Breadth improving (40%+ stocks above 200MA)
- First: buy VRT, AMAT, CDNS (they recover fastest)

═══════════════════════════════════════════
CURRENT REGIME ASSESSMENT PROTOCOL
═══════════════════════════════════════════
Every decision must start with regime identification:

STEP 1: Check SPY vs 200MA
STEP 2: Check VIX level and trend
STEP 3: Check DXY direction
STEP 4: Check 10yr yield direction
STEP 5: Determine regime (BULL / NEUTRAL / BEAR / CAPITULATION)
STEP 6: Apply appropriate playbook for that regime
STEP 7: Size positions according to regime risk level

Only THEN pick individual stocks.

This is what separates professional traders from retail — regime awareness before stock picking.

You must respond ONLY with valid JSON array. No markdown, no preamble."""

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

    memory_context = get_memory_context()

    prompt = f"""Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}

YOUR TRADE HISTORY & LESSONS:
{memory_context}

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
                "model":      "claude-opus-4-8",
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
