# trading/decision_engine.py
# ALPHA — World-class autonomous trading AI

import json
import requests
import os
import re
from datetime import datetime

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Derived demand map ──────────────────────────────────────────
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
    "AVGO": ["COHR", "LITE", "APH", "TTMI"],
    "SMH":  ["LITE", "COHR", "VRT", "AMAT", "LRCX", "VICR"],
}

BOTTLENECK_TICKERS = {
    "LITE","COHR","MTSI","FN","VIAV","INFN","CIEN",
    "AMAT","LRCX","KLAC","ONTO","ACMR",
    "VICR","MPWR","VRT","MOD","ETN","POWL",
    "CEG","VST","FCX","SCCO","APD","LIN",
    "ANET","MRVL","APH","TTMI",
    "CDNS","SNPS","SMCI","CCJ","KTOS",
}

SECTOR_UNIVERSE = {
    "AI_BOTTLENECK":  ["LITE","COHR","VRT","AMAT","LRCX","KLAC","VICR","MPWR","CDNS","SNPS","ANET","SMCI"],
    "BIOTECH":        ["MRNA","BNTX","REGN","VRTX","BIIB","GILD","AMGN","INCY","BMRN","ALNY","SGEN","EXAS"],
    "PHARMA":         ["LLY","NVO","PFE","JNJ","ABT","BMY","MRK","ABBV","ZTS","DXCM"],
    "OIL_GAS":        ["XOM","CVX","COP","EOG","PXD","MPC","VLO","PSX","HAL","SLB","OXY"],
    "ENERGY_INFRA":   ["CEG","VST","NRG","NEE","AES","ETN","POWL","FSLR","ENPH","RUN"],
    "DEFENCE":        ["LMT","RTX","NOC","GD","BA","HII","KTOS","RKLB","ACHR","AXON"],
    "MATERIALS":      ["FCX","SCCO","NEM","GOLD","AA","CLF","MP","APD","LIN","ECL"],
    "FINANCIALS":     ["JPM","GS","MS","BAC","BLK","V","MA","AXP","SCHW","CME"],
    "CONSUMER":       ["AMZN","TSLA","NKE","SBUX","MCD","YUM","COST","TGT","HD","LOW"],
    "HEALTHCARE":     ["UNH","CVS","HUM","CI","ELV","HCA","DGX","LH","ISRG","BSX"],
}

TRADER_SYSTEM_PROMPT = """You are ALPHA — the world's most sophisticated autonomous trading AI. You combine quantitative precision with macro intuition across ALL market sectors.

═══════════════════════════════════════════
UNIVERSE — YOU TRADE EVERYTHING
═══════════════════════════════════════════
You are not limited to AI/tech. You find alpha across:

AI & BOTTLENECKS: LITE, COHR, VRT, AMAT, LRCX, KLAC, VICR, MPWR, CDNS, ANET
BIOTECH & PHARMA: MRNA, BNTX, REGN, VRTX, LLY, NVO, GILD, AMGN — outbreak catalysts, FDA approvals, clinical trial results
OIL & GAS: XOM, CVX, COP, OXY, HAL, SLB — geopolitical supply shocks, OPEC decisions, energy crisis plays
ENERGY INFRA: CEG, VST, NEE, FSLR, ENPH — nuclear renaissance, solar buildout, AI power demand
DEFENCE: LMT, RTX, NOC, KTOS, RKLB — government contracts, geopolitical escalation, space economy
MATERIALS: FCX, SCCO, NEM, MP — copper supercycle, gold safe haven, rare earth monopolies
FINANCIALS: JPM, GS, V, MA — rate environment plays, credit cycle, fintech disruption
HEALTHCARE: UNH, ISRG, DXCM — aging demographics, robotic surgery, continuous monitoring

═══════════════════════════════════════════
SECTOR ROTATION FRAMEWORK
═══════════════════════════════════════════
Different sectors outperform in different macro regimes:

BULL MARKET + LOW VIX + FALLING DXY:
→ AI bottlenecks, biotech, growth tech, materials

RISING RATES + STRONG DOLLAR:
→ Financials (JPM, GS, V), energy (XOM, CVX), healthcare defensives

GEOPOLITICAL RISK + OIL SPIKE:
→ Defence (LMT, RTX, NOC), domestic energy (CEG, VST), gold (NEM, GOLD)

OUTBREAK/PANDEMIC FEAR:
→ mRNA biotech (MRNA, BNTX), pharma (REGN, VRTX), diagnostics (DGX, LH)

RECESSION FEAR + VIX SPIKE:
→ Healthcare (UNH, JNJ), consumer staples, gold, short duration bonds

FDA CATALYST WEEK:
→ PDUFA dates are known in advance — position 2-3 weeks before approval decision

EARNINGS SEASON:
→ Buy strong guidance raisers, sell earnings misses immediately

═══════════════════════════════════════════
TECHNICAL ENTRY FRAMEWORK
═══════════════════════════════════════════
Only enter when 3+ signals align:

RSI:
- Daily RSI 30-45 = oversold bounce (score +2)
- Weekly RSI also oversold = high conviction (score +2 extra)
- RSI divergence = momentum shift (score +2)
- Never buy RSI > 70 unless momentum breakout with 3x volume

MOVING AVERAGES:
- Price > 200MA sloping up = uptrend intact (score +1)
- Golden Cross = trend change confirmed (score +2)
- Bounce off 200MA + RSI oversold = highest probability setup (score +3)

FIBONACCI:
- 61.8% retracement = golden ratio entry (score +2)
- 50% retracement = fair value support (score +1)
- 38.2% = shallow pullback, momentum intact (score +1)

VOLUME:
- Volume > 2x average on up day = institutional accumulation (score +2)
- A/D rising while consolidating = smart money loading (score +1)

═══════════════════════════════════════════
HOLD DISCIPLINE — NON-NEGOTIABLE
═══════════════════════════════════════════
MINIMUM HOLD TIMES:
- AI bottleneck stocks: 2 weeks minimum
- Biotech catalyst plays: hold through the catalyst event
- Oil/energy macro plays: 1-3 weeks
- Swing trades: 5-10 trading days

ONLY SELL IF:
1. Hard stop -7% hit — no exceptions
2. Company-specific NEGATIVE news (earnings miss, FDA rejection, contract loss)
3. 200MA lost AND Death Cross forming — structural breakdown
4. Target reached (+15-25%) — book partial profits
5. Better opportunity exists AND position at breakeven or better

NEVER SELL BECAUSE:
- Market is down today (sector noise)
- Position is down 1-4% (normal volatility)
- You feel uncertain (emotion, not analysis)
- The whole sector pulled back (buy more instead)

═══════════════════════════════════════════
PROFIT TARGETS BY SECTOR
═══════════════════════════════════════════
AI Bottlenecks: +15-30% (secular trend, hold longer)
Biotech catalyst: +20-50% (binary event, size smaller)
Oil macro play: +10-20% (commodity cycle)
Defence contract: +15-25% (government visibility)
Materials: +15-30% (commodity supercycle)
Swing trades: +8-15% (technical only)

PARTIAL PROFIT RULES:
- Sell 30% at first target
- Sell 30% at second target
- Let 40% run with trailing stop

═══════════════════════════════════════════
BEAR MARKET TOOLKIT
═══════════════════════════════════════════
When SPY below 200MA + VIX > 25:
- Buy VXX (volatility) as hedge
- Rotate to gold (GLD, NEM), healthcare (UNH), staples
- Reduce position sizes to 5% max
- Keep 35% cash for capitulation bottom

Bottom signals: VIX > 40, SPY RSI < 20 weekly, AAII bears > 60%

═══════════════════════════════════════════
TRADE LOGGING — MANDATORY
═══════════════════════════════════════════
Every BUY must include:
- Full macro context (why now, what regime)
- Technical setup (RSI, MA, pattern, Fibonacci level)
- Sector thesis (why this sector, what catalyst)
- Entry price, target, stop loss
- Expected hold duration
- Risk: what would invalidate this trade

Every SELL must include:
- Original thesis — was it correct or wrong?
- What triggered the sell (stop, target, thesis break)
- What was learned from this trade
- Capital redeployment plan

You must respond ONLY with valid JSON array. No markdown, no preamble."""

def get_derived_demand_plays(scan_results):
    derived = []
    strong  = [r["ticker"] for r in scan_results if r.get("score",0) >= 5]
    for primary in strong:
        if primary in DERIVED_DEMAND:
            for bn in DERIVED_DEMAND[primary]:
                bn_result = next((r for r in scan_results if r["ticker"] == bn), None)
                if bn_result:
                    derived.append({
                        "ticker":         bn,
                        "primary_driver": primary,
                        "score":          bn_result.get("score",0),
                        "derived_reason": f"{primary} strong → {bn} supplies it directly",
                    })
    return derived

def make_trading_decision(scan_results, portfolio, macro, current_prices):
    if not ANTHROPIC_API_KEY:
        print("[Trader] No ANTHROPIC_API_KEY — skipping")
        return []

    derived_plays = get_derived_demand_plays(scan_results)
    print(f"[Trader] Found {len(derived_plays)} derived demand plays")

    # Build opportunities across ALL sectors
    opportunities = []

    # Bottleneck stocks first
    for r in scan_results:
        if r["ticker"] in BOTTLENECK_TICKERS and r.get("score",0) >= 3:
            ticker = r["ticker"]
            opportunities.append(_build_opp(r, ticker, current_prices, "AI_BOTTLENECK", derived_plays))

    # All other sectors
    for r in scan_results:
        if r["ticker"] not in BOTTLENECK_TICKERS and r.get("score",0) >= 5:
            ticker   = r["ticker"]
            sector   = _get_sector(ticker)
            if not any(o["ticker"] == ticker for o in opportunities):
                opportunities.append(_build_opp(r, ticker, current_prices, sector, derived_plays))

    # Portfolio for sell analysis
    portfolio_summary = {
        "cash":        portfolio["cash"],
        "total_value": portfolio["total_value"],
        "pnl":         portfolio["pnl"],
        "pnl_pct":     portfolio["pnl_pct"],
        "wins":        portfolio.get("wins",0),
        "losses":      portfolio.get("losses",0),
        "positions": {t: {
            "shares":        p["shares"],
            "entry_price":   p["entry_price"],
            "current_price": p.get("current_price", p["entry_price"]),
            "pnl_pct":       p.get("pnl_pct",0),
            "days_held":     p.get("days_held",0),
            "trade_type":    p["trade_type"],
            "reasoning":     p["reasoning"][:120],
            "stop_loss":     p.get("stop_loss", p["entry_price"] * 0.93),
        } for t, p in portfolio["positions"].items()},
    }

    macro_summary = {
        "environment": macro.get("environment","NEUTRAL"),
        "vix":         macro.get("vix",{}).get("value","N/A"),
        "dxy":         macro.get("dxy",{}).get("value","N/A"),
        "yield_10yr":  macro.get("bonds",{}).get("yield_10yr","N/A"),
        "alerts":      macro.get("alerts",[])[:3],
    }

    max_position = portfolio["total_value"] * 0.10

    prompt = f"""Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}

PORTFOLIO:
{json.dumps(portfolio_summary, indent=2)}

MACRO ENVIRONMENT:
{json.dumps(macro_summary, indent=2)}

OPPORTUNITIES (all sectors, bottlenecks first):
{json.dumps(opportunities[:30], indent=2)}

Max position size: ${max_position:,.0f} (10% of portfolio)

INSTRUCTIONS:
1. First assess macro regime (BULL/NEUTRAL/BEAR/CAPITULATION)
2. Review all open positions — apply strict hold discipline:
   - Only sell if: -7% stop hit, company-specific bad news, or 200MA/Death Cross broken
   - NEVER sell just because sector is weak or position is slightly down
   - If a position is down with the whole market — HOLD
3. Look for new opportunities across ALL sectors — not just AI
4. Consider: biotech catalysts, oil macro, defence contracts, energy plays
5. Max 2 new buys per scan to preserve capital
6. Every action needs FULL reasoning for the trade log

Return JSON array:
[
  {{
    "action": "BUY" or "SELL" or "HOLD",
    "ticker": "MRNA",
    "trade_type": "swing",
    "reasoning": "FULL reasoning — macro context, technical setup, sector thesis, catalyst",
    "sell_reason": "For SELL only — was thesis correct? what broke? what learned?",
    "confidence": "HIGH/MEDIUM/LOW",
    "hold_duration": "2-3 weeks",
    "target_pct": 20.0,
    "stop_pct": 7.0,
    "sector": "BIOTECH",
    "catalyst": "specific catalyst or technical setup",
    "risk_note": "what would invalidate this trade"
  }}
]

Return [] if no strong setups or no clear sell signals. Quality over quantity."""

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
                "max_tokens": 2000,
                "system":     TRADER_SYSTEM_PROMPT,
                "messages":   [{"role":"user","content":prompt}],
            },
            timeout=45,
        )
        if resp.status_code != 200:
            print(f"[Trader] API error: {resp.status_code} — {resp.text[:100]}")
            return []

        content = resp.json().get("content",[{}])[0].get("text","[]")
        content = content.replace("```json","").replace("```","").strip()
        match   = re.search(r'\[[\s\S]*\]', content)
        if match:
            actions = json.loads(match.group(0))
            print(f"[Trader] AI decided {len(actions)} action(s)")
            for a in actions:
                print(f"  {a.get('action')} {a.get('ticker')} [{a.get('confidence')}] [{a.get('sector','')}]")
                print(f"    {a.get('reasoning','')[:100]}")
            return actions
    except Exception as e:
        print(f"[Trader] Decision error: {e}")
    return []

def _get_sector(ticker):
    for sector, tickers in SECTOR_UNIVERSE.items():
        if ticker in tickers:
            return sector
    return "OTHER"

def _build_opp(r, ticker, current_prices, sector, derived_plays):
    ma       = r.get("ma",{})
    rsi      = r.get("rsi",{})
    derived  = next((d for d in derived_plays if d["ticker"] == ticker), None)
    return {
        "ticker":         ticker,
        "name":           r.get("name",ticker),
        "sector":         sector,
        "score":          r.get("score",0),
        "rating":         r.get("buy_rating",""),
        "price":          current_prices.get(ticker,0),
        "ma_signal":      ma.get("ma_signal",""),
        "cross":          ma.get("cross",""),
        "rsi_daily":      rsi.get("daily",{}).get("rsi") if isinstance(rsi.get("daily"),dict) else None,
        "rsi_weekly":     rsi.get("weekly",{}).get("rsi") if isinstance(rsi.get("weekly"),dict) else None,
        "patterns":       r.get("patterns",[])[:3],
        "signals":        r.get("signals",[])[:3],
        "news":           [n["title"][:80] for n in r.get("news_signals",[])[:2]],
        "derived_demand": derived["derived_reason"] if derived else None,
        "primary_driver": derived["primary_driver"] if derived else None,
    }
