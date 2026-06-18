#!/usr/bin/env python3
"""
MeritQuant — Autonomous AI Trader
Claude Opus 4.8 decision engine with macro brain and trade memory.
Trade windows: 9:35 AM ET (open) and 3:30 PM ET (pre-close).
GitHub Actions TRADE_MODE env var prevents timing drift failures.
"""

import os, json, time, logging, io, requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import anthropic
import yfinance as yf
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                 Table, TableStyle, HRFlowable)
from reportlab.lib.enums import TA_CENTER
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("ai_trader")

# ── Constants ─────────────────────────────────────────────────────────────────
PORTFOLIO_SIZE     = 183_000.0
MAX_POSITION_PCT   = 0.10
MAX_POSITION_USD   = PORTFOLIO_SIZE * MAX_POSITION_PCT   # $18,300
MAX_OPEN_POSITIONS = 6
STOP_LOSS_PCT      = 0.08
TAKE_PROFIT_PCT    = 0.20
MIN_CONVICTION     = 6      # Don't trade below this score

SIGNALS_FILE   = "data/signals.json"
POSITIONS_FILE = "data/positions.json"
TRADE_LOG_FILE = "data/trade_log.json"
MEMORY_FILE    = "data/memory.json"
REPORTS_DIR    = "reports"
TELEGRAM_BASE  = "https://api.telegram.org"

# ── Environment ───────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID  = os.environ["TELEGRAM_CHAT_ID"]
FRED_API_KEY      = os.environ.get("FRED_API_KEY", "")
# TRADE_MODE is set by GitHub Actions based on which cron triggered.
# "true"  → dedicated trade window schedule fired → always trade
# "false" → scan-only schedule fired → skip AI trader
TRADE_MODE = os.environ.get("TRADE_MODE", "false").lower() == "true"


# ─────────────────────────────────────────────────────────────────────────────
# 1. UTILITY HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def load_json(path: str, default=None):
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return default if default is not None else {}

def save_json(path: str, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, default=str))

PORTFOLIO_JSON_FILE = "data/portfolio.json"

def sync_portfolio_json(portfolio: dict):
    """Write portfolio.json in the format the meritquant-app frontend expects."""
    positions_dict = {}
    for p in portfolio.get("positions", []):
        ticker = p["ticker"]
        cost   = p.get("cost_basis_total", 0)
        value  = p.get("current_value", cost)
        pnl    = p.get("unrealised_pnl", 0)
        positions_dict[ticker] = {
            "ticker":      ticker,
            "name":        p.get("name", ticker),
            "shares":      p.get("shares", 0),
            "entry_price": p.get("entry_price", 0),
            "current_price": p.get("current_price", p.get("entry_price", 0)),
            "cost":        round(cost, 2),
            "value":       round(value, 2),
            "pnl":         round(pnl, 2),
            "pnl_pct":     round(p.get("unrealised_pct", 0) * 100, 2),
            "stop_loss":   round(p.get("entry_price", 0) * (1 - STOP_LOSS_PCT), 2),
            "trade_type":  "swing",
            "score":       p.get("conviction", 0),
            "reasoning":   p.get("thesis_summary", ""),
            "entry_date":  p.get("entry_date", ""),
        }

    total_value = portfolio.get("total_value", PORTFOLIO_SIZE)
    cash        = portfolio.get("cash", PORTFOLIO_SIZE)
    pnl_total   = total_value - PORTFOLIO_SIZE
    app_data = {
        "balance":      PORTFOLIO_SIZE,
        "cash":         round(cash, 2),
        "positions":    positions_dict,
        "total_value":  round(total_value, 2),
        "pnl":          round(pnl_total, 2),
        "pnl_pct":      round(pnl_total / PORTFOLIO_SIZE * 100, 2),
        "updated":      datetime.utcnow().isoformat(),
        "trades_count": len(portfolio.get("positions", [])),
    }
    save_json(PORTFOLIO_JSON_FILE, app_data)

def send_telegram(text: str, parse_mode: str = "HTML") -> bool:
    url = f"{TELEGRAM_BASE}/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode
        }, timeout=15)
        return r.ok
    except Exception as e:
        log.error(f"Telegram text failed: {e}")
        return False

def send_telegram_doc(file_bytes: bytes, filename: str, caption: str = "") -> bool:
    url = f"{TELEGRAM_BASE}/bot{TELEGRAM_TOKEN}/sendDocument"
    try:
        r = requests.post(url,
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
            files={"document": (filename, file_bytes)},
            timeout=30)
        return r.ok
    except Exception as e:
        log.error(f"Telegram doc failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 2. TRADE WINDOW CHECK  (fix: TRADE_MODE env var + ±15 min buffer)
# ─────────────────────────────────────────────────────────────────────────────
def is_trade_window() -> bool:
    """
    Returns True when AI trader should execute decisions.

    Priority 1: TRADE_MODE env var (set by GitHub Actions workflow).
      - Dedicated trade-window crons set TRADE_MODE=true
      - Scan-only crons set TRADE_MODE=false
      This eliminates GitHub Actions timing drift as a failure mode.

    Priority 2: Time-based fallback with ±15 min buffer around
      9:35 AM ET (open) and 3:30 PM ET (pre-close).
      Handles manual workflow_dispatch runs and local execution.
    """
    # Hard override from workflow scheduler
    if TRADE_MODE:
        log.info("TRADE_MODE=true — proceeding with AI trade decisions.")
        return True

    # Time-based fallback
    ET = timezone(timedelta(hours=-4))   # EDT (UTC-4); change to -5 Nov–Mar
    now_et = datetime.now(ET)

    if now_et.weekday() >= 5:            # Saturday=5, Sunday=6
        log.info("Weekend — markets closed. Skipping.")
        return False

    now_min   = now_et.hour * 60 + now_et.minute
    OPEN_WIN  = 9  * 60 + 35            # 575 minutes
    CLOSE_WIN = 15 * 60 + 30            # 930 minutes
    BUFFER    = 15                       # ±15 min handles GH Actions drift

    in_open  = abs(now_min - OPEN_WIN)  <= BUFFER
    in_close = abs(now_min - CLOSE_WIN) <= BUFFER

    if in_open or in_close:
        label = "OPEN" if in_open else "PRE-CLOSE"
        log.info(f"Trade window: {label} at {now_et.strftime('%H:%M')} ET")
        return True

    log.info(f"Scan-only window at {now_et.strftime('%H:%M')} ET — skipping AI trades.")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# 3. MACRO BRAIN
# ─────────────────────────────────────────────────────────────────────────────
def fetch_fred(series_id: str) -> Optional[float]:
    """Fetch latest value from FRED public CSV endpoint."""
    try:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        if FRED_API_KEY:
            url += f"&api_key={FRED_API_KEY}"
        r = requests.get(url, timeout=12)
        for line in reversed(r.text.strip().split("\n")[1:]):
            parts = line.split(",")
            if len(parts) == 2 and parts[1].strip() not in ("", "."):
                return float(parts[1].strip())
    except Exception as e:
        log.warning(f"FRED {series_id}: {e}")
    return None

def fetch_price(ticker: str) -> Optional[float]:
    """Fetch latest close from Yahoo Finance."""
    try:
        hist = yf.Ticker(ticker).history(period="2d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        log.warning(f"Yahoo {ticker}: {e}")
    return None

def build_macro_context() -> dict:
    log.info("Building macro brain context...")

    vix       = fetch_price("^VIX")
    dxy       = fetch_price("DX-Y.NYB")
    spy       = fetch_price("SPY")
    t10y2y    = fetch_fred("T10Y2Y")        # Yield curve
    hy_spread = fetch_fred("BAMLH0A0HYM2") # HY credit OAS bps
    t10y      = fetch_fred("DGS10")         # 10Y yield
    t2y       = fetch_fred("DGS2Y")         # 2Y yield
    fed_rate  = fetch_fred("FEDFUNDS")      # Fed funds

    # ── Regime classification ─────────────────────────────────────────────────
    regime  = "NEUTRAL"
    reasons = []

    if vix is not None:
        if vix > 30:
            regime = "RISK-OFF"
            reasons.append(f"VIX elevated at {vix:.1f} — fear spike")
        elif vix < 18:
            reasons.append(f"VIX calm at {vix:.1f} — supportive for longs")
        else:
            reasons.append(f"VIX neutral at {vix:.1f}")

    if t10y2y is not None:
        if t10y2y < 0:
            if regime != "RISK-OFF":
                regime = "CAUTION"
            reasons.append(f"Yield curve inverted at {t10y2y:.2f}% — recession signal")
        else:
            reasons.append(f"Yield curve positive at +{t10y2y:.2f}%")

    if hy_spread is not None:
        if hy_spread > 500:
            regime = "RISK-OFF"
            reasons.append(f"HY spreads wide at {hy_spread:.0f}bps — credit stress")
        elif hy_spread < 300:
            reasons.append(f"HY spreads tight at {hy_spread:.0f}bps — credit healthy")
        else:
            reasons.append(f"HY spreads neutral at {hy_spread:.0f}bps")

    if regime == "NEUTRAL" and vix and vix < 20 and (t10y2y or 0) > 0:
        regime = "RISK-ON"

    macro = {
        "timestamp": datetime.utcnow().isoformat(),
        "regime": regime,
        "regime_reasons": reasons,
        "indicators": {
            "VIX":       {"value": vix,       "label": "CBOE Volatility Index"},
            "DXY":       {"value": dxy,       "label": "US Dollar Index"},
            "SPY":       {"value": spy,       "label": "S&P 500 ETF"},
            "T10Y2Y":    {"value": t10y2y,    "label": "Yield Curve 10Y–2Y (%)"},
            "HY_SPREAD": {"value": hy_spread, "label": "HY Credit OAS (bps)"},
            "T10Y":      {"value": t10y,      "label": "10Y Treasury (%)"},
            "T2Y":       {"value": t2y,       "label": "2Y Treasury (%)"},
            "FED_RATE":  {"value": fed_rate,  "label": "Fed Funds Rate (%)"},
        }
    }

    log.info(f"Macro: {regime} | VIX={vix} | Curve={t10y2y} | HY={hy_spread}bps")
    return macro


# ─────────────────────────────────────────────────────────────────────────────
# 4. TRADE MEMORY SYSTEM
# ─────────────────────────────────────────────────────────────────────────────
def load_memory() -> dict:
    return load_json(MEMORY_FILE, {
        "losing_trades": [],
        "winning_patterns": [],
        "macro_lessons": [],
        "last_updated": None
    })

def save_memory(memory: dict):
    memory["last_updated"] = datetime.utcnow().isoformat()
    save_json(MEMORY_FILE, memory)

def update_memory(trade: dict, memory: dict) -> dict:
    pnl = trade.get("pnl_pct", 0)
    if pnl < -0.05:
        memory["losing_trades"].append({
            "ticker":     trade.get("ticker"),
            "date":       trade.get("exit_date", "")[:10],
            "pnl_pct":    round(pnl, 4),
            "why_failed": trade.get("exit_reason", ""),
            "thesis":     trade.get("thesis_summary", ""),
            "lesson":     trade.get("lesson", "Verify thesis confirmation before full sizing.")
        })
        memory["losing_trades"] = memory["losing_trades"][-20:]
    elif pnl > 0.10:
        memory["winning_patterns"].append({
            "ticker":   trade.get("ticker"),
            "date":     trade.get("exit_date", "")[:10],
            "pnl_pct":  round(pnl, 4),
            "setup":    trade.get("technical_setup", ""),
            "catalyst": trade.get("catalyst", ""),
        })
        memory["winning_patterns"] = memory["winning_patterns"][-20:]
    return memory

def memory_to_prompt(memory: dict) -> str:
    parts = []
    if memory.get("losing_trades"):
        parts.append("PAST LOSING TRADES — DO NOT REPEAT:")
        for t in memory["losing_trades"][-5:]:
            parts.append(f"  • {t['ticker']} ({t['date']}) | {t['pnl_pct']*100:.1f}% | "
                         f"Why: {t['why_failed']} | Lesson: {t['lesson']}")
    if memory.get("winning_patterns"):
        parts.append("\nWINNING SETUPS TO REPLICATE:")
        for p in memory["winning_patterns"][-3:]:
            parts.append(f"  • {p['ticker']} ({p['date']}) | +{p['pnl_pct']*100:.1f}% | "
                         f"Setup: {p['setup']} | Catalyst: {p['catalyst']}")
    return "\n".join(parts) if parts else "No significant trade history recorded yet."


# ─────────────────────────────────────────────────────────────────────────────
# 5. PORTFOLIO STATE MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────
def load_positions() -> dict:
    return load_json(POSITIONS_FILE, {
        "positions": [],
        "cash": PORTFOLIO_SIZE,
        "total_value": PORTFOLIO_SIZE,
        "last_updated": None
    })

def load_trade_log() -> list:
    return load_json(TRADE_LOG_FILE, [])

def update_prices(portfolio: dict) -> dict:
    total = portfolio.get("cash", 0)
    for pos in portfolio.get("positions", []):
        price = fetch_price(pos["ticker"])
        if price:
            pos["current_price"]  = price
            pos["current_value"]  = price * pos["shares"]
            pos["unrealised_pnl"] = pos["current_value"] - pos["cost_basis_total"]
            pos["unrealised_pct"] = pos["unrealised_pnl"] / pos["cost_basis_total"]
        total += pos.get("current_value", pos["cost_basis_total"])
    portfolio["total_value"]  = total
    portfolio["last_updated"] = datetime.utcnow().isoformat()
    return portfolio

def close_pos(pos: dict, reason: str) -> dict:
    return {
        "type":             "CLOSE",
        "ticker":           pos["ticker"],
        "shares":           pos["shares"],
        "entry_price":      pos["entry_price"],
        "exit_price":       pos.get("current_price", pos["entry_price"]),
        "cost_basis_total": pos["cost_basis_total"],
        "exit_value":       pos.get("current_value", pos["cost_basis_total"]),
        "pnl_usd":          pos.get("unrealised_pnl", 0),
        "pnl_pct":          pos.get("unrealised_pct", 0),
        "exit_reason":      reason,
        "thesis_summary":   pos.get("thesis_summary", ""),
        "catalyst":         pos.get("catalyst", ""),
        "technical_setup":  pos.get("technical_setup", ""),
        "entry_date":       pos.get("entry_date", ""),
        "exit_date":        datetime.utcnow().isoformat(),
        "trade_date":       datetime.utcnow().isoformat(),
    }

def run_sl_tp(portfolio: dict, trade_log: list, memory: dict):
    """Auto-exit positions at stop loss (−8%) or take profit (+20%)."""
    exits, remaining = [], []
    for pos in portfolio.get("positions", []):
        pnl = pos.get("unrealised_pct", 0)
        if pnl <= -STOP_LOSS_PCT:
            reason = f"STOP LOSS at {pnl*100:.1f}%"
            t = close_pos(pos, reason)
            trade_log.append(t)
            memory = update_memory(t, memory)
            portfolio["cash"] += pos.get("current_value", pos["cost_basis_total"])
            exits.append((pos["ticker"], reason, pnl))
            log.warning(f"SL HIT: {pos['ticker']} {pnl*100:.1f}%")
        elif pnl >= TAKE_PROFIT_PCT:
            reason = f"TAKE PROFIT at +{pnl*100:.1f}%"
            t = close_pos(pos, reason)
            trade_log.append(t)
            portfolio["cash"] += pos.get("current_value", pos["cost_basis_total"])
            exits.append((pos["ticker"], reason, pnl))
            log.info(f"TP HIT: {pos['ticker']} +{pnl*100:.1f}%")
        else:
            remaining.append(pos)
    portfolio["positions"] = remaining
    return portfolio, trade_log, memory, exits


# ─────────────────────────────────────────────────────────────────────────────
# 6. CLAUDE OPUS 4.8 DECISION ENGINE
# ─────────────────────────────────────────────────────────────────────────────
def build_prompt(signals: list, macro: dict, portfolio: dict, memory: dict) -> str:
    ind = macro.get("indicators", {})

    def v(key):
        val = ind.get(key, {}).get("value")
        return f"{val:.2f}" if val is not None else "N/A"

    macro_block = f"""MACRO ENVIRONMENT — {macro['regime']} REGIME
{chr(10).join(f'  ▸ {r}' for r in macro.get('regime_reasons', []))}
  VIX: {v('VIX')}  |  DXY: {v('DXY')}  |  SPY: ${v('SPY')}
  Yield Curve (10Y-2Y): {v('T10Y2Y')}%  |  HY Credit Spread: {v('HY_SPREAD')}bps
  10Y Treasury: {v('T10Y')}%  |  Fed Funds: {v('FED_RATE')}%"""

    positions = portfolio.get("positions", [])
    port_block = (
        f"Cash: ${portfolio.get('cash', 0):,.0f}  |  "
        f"Total: ${portfolio.get('total_value', 0):,.0f}  |  "
        f"Positions: {len(positions)}/{MAX_OPEN_POSITIONS}\n"
    )
    for p in positions:
        port_block += (
            f"  {p['ticker']}: {p['shares']:.1f}sh @ ${p['entry_price']:.2f} → "
            f"${p.get('current_price', p['entry_price']):.2f} | "
            f"P&L {p.get('unrealised_pct', 0)*100:+.1f}% | "
            f"Thesis: {p.get('thesis_summary', '')[:60]}\n"
        )

    top_signals = sorted(signals, key=lambda x: x.get("score", 0), reverse=True)[:12]
    sig_block = "TOP SIGNALS FROM SCANNER:\n"
    for s in top_signals:
        sig_block += (
            f"  {s.get('ticker','?'):6s} | Score {s.get('score',0):4.1f} | "
            f"RSI {s.get('rsi','N/A'):5s} | {s.get('signal','?'):4s} | "
            f"Sector: {s.get('sector','?'):15s} | "
            f"{s.get('news_headline','')[:70]}\n"
        )

    window = "PRE-CLOSE (3:30 PM ET)" if datetime.utcnow().hour >= 19 else "OPEN (9:35 AM ET)"

    return f"""You are MeritQuant's autonomous AI portfolio manager — institutional grade, Goldman Sachs calibre.
Portfolio: ${PORTFOLIO_SIZE:,.0f} paper mirror of a real NGO trust. Trade window: {window}.

FRAMEWORK:
- Buffett balance sheet discipline + macro catalyst ID + technical confirmation
- RSI, moving averages, chart patterns as entry/exit triggers
- Max {MAX_POSITION_PCT*100:.0f}% per position (${MAX_POSITION_USD:,.0f}) | Max {MAX_OPEN_POSITIONS} concurrent
- Stop loss: {STOP_LOSS_PCT*100:.0f}% | Take profit: {TAKE_PROFIT_PCT*100:.0f}%
- Min conviction score to trade: {MIN_CONVICTION}/10
- DO NOT enter in RISK-OFF regime unless position is a hedge (VXX, GLD, TLT)
- NEVER chase momentum — catalyst + chart must both confirm

{macro_block}

PORTFOLIO STATE:
{port_block}
{sig_block}

TRADE MEMORY:
{memory_to_prompt(memory)}

TASK:
1. Read macro regime. If RISK-OFF, only hedges allowed.
2. Review signals against existing positions. Avoid sector overlap.
3. Decide: ENTER (new position), HOLD (existing), or EXIT (thesis broken/hit target).
4. Apply memory lessons — no repeating documented mistakes.
5. For each action provide full structured rationale.

RESPOND ONLY with valid JSON — no preamble, no markdown fences:
{{
  "market_assessment": "2-3 sentence macro read",
  "regime": "RISK-ON|RISK-OFF|NEUTRAL|CAUTION",
  "actions": [
    {{
      "ticker": "XXXX",
      "action": "ENTER|EXIT|HOLD",
      "position_size_usd": 18000,
      "conviction": 8,
      "thesis": "2-3 sentence investment case.",
      "catalyst": "Primary catalyst.",
      "technical_setup": "RSI level, pattern, MA alignment.",
      "risk_factors": "Key risks.",
      "tier": "1|2|3|4"
    }}
  ],
  "portfolio_notes": "Overall portfolio management comment.",
  "memory_applied": "Which past lessons influenced decisions today."
}}"""

def call_claude(prompt: str) -> Optional[dict]:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    try:
        log.info("Calling Claude Opus 4.8...")
        msg = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip()
        # Strip any accidental markdown fences
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        decision = json.loads(raw.strip())
        log.info(f"Decision: regime={decision.get('regime')} | actions={len(decision.get('actions',[]))}")
        return decision
    except json.JSONDecodeError as e:
        log.error(f"JSON parse error: {e}")
    except Exception as e:
        log.error(f"Claude API error: {e}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 7. TRADE EXECUTOR
# ─────────────────────────────────────────────────────────────────────────────
def execute(decision: dict, portfolio: dict, trade_log: list) -> tuple:
    actions_taken = []
    held_tickers  = {p["ticker"] for p in portfolio.get("positions", [])}

    for action in decision.get("actions", []):
        ticker     = action.get("ticker", "").upper().strip()
        act        = action.get("action", "HOLD").upper()
        size_usd   = min(float(action.get("position_size_usd", 0)), MAX_POSITION_USD)
        conviction = int(action.get("conviction", 5))

        if not ticker:
            continue

        if act == "ENTER":
            if ticker in held_tickers:
                log.info(f"SKIP {ticker}: already held.")
                continue
            if len(portfolio["positions"]) >= MAX_OPEN_POSITIONS:
                log.warning(f"SKIP {ticker}: max positions reached.")
                continue
            if portfolio["cash"] < size_usd:
                log.warning(f"SKIP {ticker}: insufficient cash.")
                continue
            if conviction < MIN_CONVICTION:
                log.info(f"SKIP {ticker}: conviction {conviction} < {MIN_CONVICTION}.")
                continue

            price = fetch_price(ticker)
            if not price:
                log.error(f"SKIP {ticker}: price unavailable.")
                continue

            shares   = size_usd / price
            position = {
                "ticker":           ticker,
                "shares":           shares,
                "entry_price":      price,
                "cost_basis_total": size_usd,
                "current_price":    price,
                "current_value":    size_usd,
                "unrealised_pnl":   0.0,
                "unrealised_pct":   0.0,
                "thesis_summary":   action.get("thesis", ""),
                "catalyst":         action.get("catalyst", ""),
                "technical_setup":  action.get("technical_setup", ""),
                "risk_factors":     action.get("risk_factors", ""),
                "conviction":       conviction,
                "tier":             action.get("tier", "2"),
                "entry_date":       datetime.utcnow().isoformat(),
            }
            portfolio["positions"].append(position)
            portfolio["cash"] -= size_usd
            held_tickers.add(ticker)

            trade_log.append({**position, "type": "ENTER",
                               "trade_date": datetime.utcnow().isoformat()})
            actions_taken.append(action)
            log.info(f"ENTER {ticker}: {shares:.2f}sh @ ${price:.2f} | Conv {conviction}/10")

        elif act == "EXIT":
            for i, pos in enumerate(portfolio["positions"]):
                if pos["ticker"] == ticker:
                    price = fetch_price(ticker) or pos.get("current_price", pos["entry_price"])
                    pos.update({
                        "current_price":  price,
                        "current_value":  price * pos["shares"],
                        "unrealised_pnl": (price * pos["shares"]) - pos["cost_basis_total"],
                        "unrealised_pct": ((price * pos["shares"]) - pos["cost_basis_total"]) / pos["cost_basis_total"],
                    })
                    t = close_pos(pos, action.get("thesis", "AI exit decision"))
                    t["type"] = "EXIT"
                    trade_log.append(t)
                    portfolio["cash"] += pos["current_value"]
                    portfolio["positions"].pop(i)
                    held_tickers.discard(ticker)
                    actions_taken.append(action)
                    log.info(f"EXIT {ticker}: P&L {pos['unrealised_pct']*100:+.1f}%")
                    break

    return portfolio, trade_log, actions_taken


# ─────────────────────────────────────────────────────────────────────────────
# 8. PDF REPORT  (institutional quality)
# ─────────────────────────────────────────────────────────────────────────────
NAVY  = colors.HexColor("#0d1f3c")
BLUE  = colors.HexColor("#2a6db5")
GREEN = colors.HexColor("#1a6e3e")
RED   = colors.HexColor("#9e2020")
LIGHT = colors.HexColor("#f4f8fd")
AMBER = colors.HexColor("#d07a10")

def build_pdf(decision: dict, portfolio: dict, macro: dict,
              actions: list, auto_exits: list) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            leftMargin=0.75*inch, rightMargin=0.75*inch,
                            topMargin=0.75*inch, bottomMargin=0.75*inch)
    styles  = getSampleStyleSheet()
    body    = ParagraphStyle("body",  parent=styles["Normal"], fontSize=9,  leading=14)
    title_s = ParagraphStyle("title", parent=styles["Normal"], fontSize=18, textColor=NAVY, spaceAfter=4, fontName="Helvetica-Bold")
    sub_s   = ParagraphStyle("sub",   parent=styles["Normal"], fontSize=10, textColor=BLUE, spaceAfter=12)
    h2_s    = ParagraphStyle("h2",    parent=styles["Normal"], fontSize=12, textColor=NAVY, spaceBefore=14, spaceAfter=6, fontName="Helvetica-Bold")
    small   = ParagraphStyle("small", parent=styles["Normal"], fontSize=8,  textColor=colors.grey, alignment=TA_CENTER)

    now_str = datetime.utcnow().strftime("%d %b %Y · %H:%M UTC")
    window  = "PRE-CLOSE · 3:30 PM ET" if datetime.utcnow().hour >= 19 else "OPEN · 9:35 AM ET"
    regime  = decision.get("regime", macro.get("regime", "NEUTRAL"))
    rc = {"RISK-ON": GREEN, "RISK-OFF": RED, "CAUTION": AMBER}.get(regime, BLUE)

    story = [
        Paragraph("MeritQuant — Autonomous Trade Report", title_s),
        Paragraph(f"{window}  ·  {now_str}", sub_s),
        HRFlowable(width="100%", thickness=2, color=BLUE),
        Spacer(1, 8),

        Paragraph("Market Assessment", h2_s),
        Paragraph(decision.get("market_assessment", "—"), body),
        Spacer(1, 4),
        Table([[Paragraph(f"<b>REGIME: {regime}</b>",
                ParagraphStyle("rp", parent=body, textColor=rc, fontName="Helvetica-Bold"))]],
              colWidths=[6.5*inch],
              style=[("BACKGROUND",(0,0),(-1,-1),LIGHT),
                     ("BOX",(0,0),(-1,-1),1.5,rc),
                     ("TOPPADDING",(0,0),(-1,-1),8),
                     ("BOTTOMPADDING",(0,0),(-1,-1),8)]),
        Spacer(1, 12),
    ]

    # Macro snapshot table
    ind = macro.get("indicators", {})
    def iv(k): 
        val = ind.get(k, {}).get("value")
        return f"{val:.2f}" if val is not None else "—"

    story.append(Paragraph("Macro Snapshot", h2_s))
    mt = Table([
        ["Indicator", "Value", "Indicator", "Value"],
        ["VIX",              iv("VIX"),      "US Dollar (DXY)",   iv("DXY")],
        ["Yield Curve 10Y-2Y", iv("T10Y2Y"), "HY Credit (bps)",   iv("HY_SPREAD")],
        ["10Y Treasury (%)", iv("T10Y"),     "Fed Funds Rate (%)", iv("FED_RATE")],
    ], colWidths=[2.1*inch, 1.1*inch, 2.1*inch, 1.2*inch])
    mt.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),NAVY), ("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
        ("FONTSIZE",(0,0),(-1,-1),8.5),
        ("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#dde5f0")),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, LIGHT]),
        ("TOPPADDING",(0,0),(-1,-1),5), ("BOTTOMPADDING",(0,0),(-1,-1),5),
    ]))
    story += [mt, Spacer(1, 12)]

    # Actions
    if actions:
        story.append(Paragraph("Trade Decisions", h2_s))
        for a in actions:
            act  = a.get("action", "")
            ac   = GREEN if act == "ENTER" else RED if act == "EXIT" else BLUE
            story.append(Table([[
                Paragraph(f"<b>{act} — {a.get('ticker')}</b>",
                           ParagraphStyle("ah", parent=body, textColor=ac, fontName="Helvetica-Bold")),
                Paragraph(f"${a.get('position_size_usd',0):,.0f}  |  Conviction {a.get('conviction','—')}/10  |  Tier {a.get('tier','?')}",
                           ParagraphStyle("am", parent=body, alignment=1))
            ]], colWidths=[3.25*inch, 3.25*inch],
               style=[("BACKGROUND",(0,0),(-1,-1),LIGHT), ("BOX",(0,0),(-1,-1),1,ac),
                      ("TOPPADDING",(0,0),(-1,-1),6), ("BOTTOMPADDING",(0,0),(-1,-1),6)]))
            story.append(Spacer(1, 4))
            for label, key in [("Thesis", "thesis"), ("Catalyst", "catalyst"),
                                ("Technical Setup", "technical_setup"), ("Risk Factors", "risk_factors")]:
                story.append(Paragraph(f"<b>{label}:</b> {a.get(key, '—')}", body))
            story.append(Spacer(1, 8))

    # Auto-exits
    if auto_exits:
        story.append(Paragraph("Automatic Exits (SL / TP)", h2_s))
        for ticker, reason, pnl in auto_exits:
            c = GREEN if pnl > 0 else RED
            story.append(Paragraph(
                f"<b>{ticker}</b>: {reason}",
                ParagraphStyle("ex", parent=body, textColor=c)))

    # Portfolio snapshot
    story.append(Paragraph("Portfolio Snapshot", h2_s))
    story.append(Paragraph(
        f"Total: <b>${portfolio.get('total_value',0):,.0f}</b>  |  "
        f"Cash: <b>${portfolio.get('cash',0):,.0f}</b>  |  "
        f"Positions: <b>{len(portfolio.get('positions',[]))}/{MAX_OPEN_POSITIONS}</b>", body))

    if portfolio.get("positions"):
        ph = [["Ticker","Shares","Entry","Current","Value","P&L $","P&L %"]]
        for p in portfolio["positions"]:
            ph.append([
                p["ticker"], f"{p['shares']:.1f}",
                f"${p['entry_price']:.2f}",
                f"${p.get('current_price', p['entry_price']):.2f}",
                f"${p.get('current_value',0):,.0f}",
                f"${p.get('unrealised_pnl',0):+,.0f}",
                f"{p.get('unrealised_pct',0)*100:+.1f}%",
            ])
        pt = Table(ph, colWidths=[0.8*inch]*7)
        pt.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),NAVY), ("TEXTCOLOR",(0,0),(-1,0),colors.white),
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
            ("FONTSIZE",(0,0),(-1,-1),8),
            ("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#dde5f0")),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,LIGHT]),
            ("ALIGN",(1,0),(-1,-1),"CENTER"),
            ("TOPPADDING",(0,0),(-1,-1),5), ("BOTTOMPADDING",(0,0),(-1,-1),5),
        ]))
        story.append(pt)

    if decision.get("portfolio_notes"):
        story += [Spacer(1,8), Paragraph(f"<b>Notes:</b> {decision['portfolio_notes']}", body)]
    if decision.get("memory_applied"):
        story += [Paragraph(f"<b>Memory applied:</b> {decision['memory_applied']}", body)]

    story += [Spacer(1,16), HRFlowable(width="100%",thickness=1,color=BLUE),
              Paragraph("MeritQuant Autonomous Trader · Paper Portfolio · Not financial advice", small)]
    doc.build(story)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# 9. EXCEL REPORT
# ─────────────────────────────────────────────────────────────────────────────
def build_excel(decision: dict, portfolio: dict, trade_log: list) -> bytes:
    wb   = openpyxl.Workbook()
    NAVY_H, BLUE_H, GREEN_H, RED_H, LIGHT_H = "0D1F3C","2A6DB5","1A6E3E","9E2020","F4F8FD"

    def hdr(ws, row, ncols, text, fill=None):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
        c = ws.cell(row=row, column=1, value=text)
        c.fill = PatternFill("solid", fgColor=fill or NAVY_H)
        c.font = Font(color="FFFFFF", bold=True, size=10)
        c.alignment = Alignment(horizontal="center")

    # ── Sheet 1: Summary ──────────────────────────────────────────────────────
    ws = wb.active
    ws.title = "Summary"
    hdr(ws, 1, 2, "MeritQuant — AI Trade Session Report")
    rows = [
        ("Generated UTC", datetime.utcnow().strftime("%Y-%m-%d %H:%M")),
        ("Portfolio Value", f"${portfolio.get('total_value',0):,.2f}"),
        ("Cash Available", f"${portfolio.get('cash',0):,.2f}"),
        ("Open Positions", f"{len(portfolio.get('positions',[]))}/{MAX_OPEN_POSITIONS}"),
        ("Regime", decision.get("regime","?")),
        ("Market Assessment", decision.get("market_assessment","—")),
        ("Memory Applied", decision.get("memory_applied","—")),
        ("Portfolio Notes", decision.get("portfolio_notes","—")),
    ]
    for r, (k, v) in enumerate(rows, 2):
        ws.cell(row=r, column=1, value=k).font = Font(bold=True, size=9)
        ws.cell(row=r, column=2, value=v).font = Font(size=9)
        if r % 2 == 0:
            for c in range(1, 3):
                ws.cell(row=r, column=c).fill = PatternFill("solid", fgColor=LIGHT_H)
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 55

    # ── Sheet 2: Positions ────────────────────────────────────────────────────
    ws2 = wb.create_sheet("Positions")
    cols = ["Ticker","Shares","Entry $","Current $","Value $","P&L $","P&L %",
            "Conviction","Tier","Catalyst","Technical Setup","Entry Date"]
    for c, h in enumerate(cols, 1):
        cell = ws2.cell(row=1, column=c, value=h)
        cell.fill = PatternFill("solid", fgColor=NAVY_H)
        cell.font = Font(color="FFFFFF", bold=True, size=9)
        ws2.column_dimensions[cell.column_letter].width = 16
    for r, p in enumerate(portfolio.get("positions",[]), 2):
        pnl = p.get("unrealised_pct", 0)
        row_vals = [
            p["ticker"], round(p["shares"],2), round(p["entry_price"],2),
            round(p.get("current_price", p["entry_price"]),2),
            round(p.get("current_value",0),2), round(p.get("unrealised_pnl",0),2),
            f"{pnl*100:.1f}%", p.get("conviction","—"), p.get("tier","—"),
            p.get("catalyst",""), p.get("technical_setup",""),
            p.get("entry_date","")[:10]
        ]
        for c, val in enumerate(row_vals, 1):
            cell = ws2.cell(row=r, column=c, value=val)
            cell.font = Font(size=9,
                             color=(GREEN_H if pnl >= 0 else RED_H) if c == 7 else "000000",
                             bold=(c == 7))
        if r % 2 == 0:
            for c in range(1, len(cols)+1):
                ws2.cell(row=r, column=c).fill = PatternFill("solid", fgColor=LIGHT_H)

    # ── Sheet 3: Trade Log ────────────────────────────────────────────────────
    ws3 = wb.create_sheet("Trade Log")
    log_cols = ["Date","Type","Ticker","Shares","Entry $","Exit $","P&L $","P&L %","Reason"]
    for c, h in enumerate(log_cols, 1):
        cell = ws3.cell(row=1, column=c, value=h)
        cell.fill = PatternFill("solid", fgColor=NAVY_H)
        cell.font = Font(color="FFFFFF", bold=True, size=9)
        ws3.column_dimensions[cell.column_letter].width = 14
    for r, t in enumerate(reversed(trade_log[-100:]), 2):
        pnl = t.get("pnl_pct", 0)
        row_vals = [
            str(t.get("trade_date", t.get("entry_date","—")))[:10],
            t.get("type","—"), t.get("ticker","—"),
            round(t.get("shares",0),2), round(t.get("entry_price",0),2),
            round(t.get("exit_price",0),2), round(t.get("pnl_usd",0),2),
            f"{pnl*100:.1f}%" if pnl else "—",
            str(t.get("exit_reason", t.get("thesis_summary","")))[:80],
        ]
        for c, val in enumerate(row_vals, 1):
            ws3.cell(row=r, column=c, value=val).font = Font(size=9)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# 10. TELEGRAM MESSAGES
# ─────────────────────────────────────────────────────────────────────────────
def trade_msg(action: dict, portfolio: dict) -> str:
    act = action.get("action","")
    e   = {"ENTER":"🟢","EXIT":"🔴","HOLD":"⚪️"}.get(act,"⚪️")
    return (
        f"{e} <b>MeritQuant — {act} {action.get('ticker')}</b>\n\n"
        f"💰 Size: <b>${action.get('position_size_usd',0):,.0f}</b>\n"
        f"🎯 Conviction: <b>{action.get('conviction','—')}/10</b>  |  Tier {action.get('tier','?')}\n\n"
        f"📋 <b>Thesis:</b>\n{action.get('thesis','—')}\n\n"
        f"⚡️ <b>Catalyst:</b> {action.get('catalyst','—')}\n"
        f"📊 <b>Setup:</b> {action.get('technical_setup','—')}\n"
        f"⚠️ <b>Risk:</b> {action.get('risk_factors','—')}\n\n"
        f"💼 Portfolio: ${portfolio.get('total_value',0):,.0f}  |  Cash: ${portfolio.get('cash',0):,.0f}"
    )

def sl_tp_msg(ticker: str, reason: str, pnl: float) -> str:
    e = "✅" if pnl > 0 else "🛑"
    return f"{e} <b>MeritQuant — AUTO EXIT {ticker}</b>\n📋 {reason}\n💰 P&L: {pnl*100:+.1f}%"

def session_msg(decision: dict, portfolio: dict, actions: list,
                auto_exits: list, macro: dict) -> str:
    window = "🌅 OPEN 9:35 AM ET" if datetime.utcnow().hour < 19 else "🌆 PRE-CLOSE 3:30 PM ET"
    re     = {"RISK-ON":"🟢","RISK-OFF":"🔴","CAUTION":"🟡"}.get(decision.get("regime"),"⚪️")
    ind    = macro.get("indicators", {})
    vix    = ind.get("VIX",{}).get("value")
    curve  = ind.get("T10Y2Y",{}).get("value")
    hy     = ind.get("HY_SPREAD",{}).get("value")

    lines = [
        f"📊 <b>MeritQuant Session — {window}</b>",
        f"\n{re} <b>Regime: {decision.get('regime','NEUTRAL')}</b>",
        f"💬 {decision.get('market_assessment','—')}",
    ]
    if all(x is not None for x in [vix, curve, hy]):
        lines.append(f"\n📈 VIX <b>{vix:.1f}</b>  |  Curve <b>{curve:+.2f}%</b>  |  HY <b>{hy:.0f}bps</b>")

    lines.append(f"\n🔢 <b>Actions: {len(actions)}</b>")
    for a in actions:
        e = "🟢" if a["action"]=="ENTER" else "🔴"
        lines.append(f"  {e} {a['action']} {a['ticker']} — ${a.get('position_size_usd',0):,.0f} | {a.get('conviction',0)}/10")

    if auto_exits:
        lines.append(f"\n⚡️ <b>Auto exits: {len(auto_exits)}</b>")
        for t, r, p in auto_exits:
            lines.append(f"  {'✅' if p>0 else '🛑'} {t}: {p*100:+.1f}%")

    lines += [
        f"\n💼 <b>${portfolio.get('total_value',0):,.0f}</b> total",
        f"💵 Cash: ${portfolio.get('cash',0):,.0f}",
        f"📂 Positions: {len(portfolio.get('positions',[]))}/{MAX_OPEN_POSITIONS}",
    ]
    if decision.get("memory_applied"):
        lines.append(f"\n🧠 {decision['memory_applied']}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 11. MAIN ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("MeritQuant AI Trader — session start")
    log.info(f"TRADE_MODE={TRADE_MODE} | UTC={datetime.utcnow().strftime('%H:%M')}")
    log.info("=" * 60)

    if not is_trade_window():
        return

    Path("data").mkdir(exist_ok=True)
    Path(REPORTS_DIR).mkdir(exist_ok=True)

    # Load state
    signals   = load_json(SIGNALS_FILE,   [])
    portfolio = load_positions()
    trade_log = load_trade_log()
    memory    = load_memory()

    if not signals:
        log.warning("No signals file — running with empty signal list.")

    # Build macro context
    macro = build_macro_context()

    # Refresh prices on open positions
    portfolio = update_prices(portfolio)

    # Auto SL / TP check
    portfolio, trade_log, memory, auto_exits = run_sl_tp(portfolio, trade_log, memory)
    for ticker, reason, pnl in auto_exits:
        send_telegram(sl_tp_msg(ticker, reason, pnl))
        time.sleep(1)

    # Claude Opus decision
    prompt   = build_prompt(signals, macro, portfolio, memory)
    decision = call_claude(prompt)

    if not decision:
        send_telegram("⚠️ <b>MeritQuant</b>: Claude returned no valid decision. Manual review required.")
        log.error("No valid decision — aborting.")
        return

    # Execute
    portfolio, trade_log, actions_taken = execute(decision, portfolio, trade_log)

    # Persist state
    save_json(POSITIONS_FILE, portfolio)
    sync_portfolio_json(portfolio)
    save_json(TRADE_LOG_FILE, trade_log)
    save_memory(memory)

    # Individual trade alerts
    for a in actions_taken:
        if a.get("action") in ("ENTER", "EXIT"):
            send_telegram(trade_msg(a, portfolio))
            time.sleep(1)

    # Generate and send reports
    ts         = datetime.utcnow().strftime("%Y%m%d_%H%M")
    pdf_bytes  = build_pdf(decision, portfolio, macro, actions_taken, auto_exits)
    xlsx_bytes = build_excel(decision, portfolio, trade_log)
    pdf_name   = f"MeritQuant_{ts}.pdf"
    xlsx_name  = f"MeritQuant_{ts}.xlsx"

    (Path(REPORTS_DIR) / pdf_name).write_bytes(pdf_bytes)
    (Path(REPORTS_DIR) / xlsx_name).write_bytes(xlsx_bytes)

    send_telegram_doc(pdf_bytes,  pdf_name,  f"📄 Trade Report {ts}")
    time.sleep(2)
    send_telegram_doc(xlsx_bytes, xlsx_name, f"📊 Trade Log {ts}")
    time.sleep(1)

    # Session summary
    send_telegram(session_msg(decision, portfolio, actions_taken, auto_exits, macro))

    log.info(f"Session complete — actions={len(actions_taken)}, auto_exits={len(auto_exits)}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
