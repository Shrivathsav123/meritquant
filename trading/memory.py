# trading/memory.py — AI persistent memory and lesson system
import json
import os
from datetime import datetime

DATA_DIR    = "data"
MEMORY_FILE = f"{DATA_DIR}/ai_memory.json"

def load_memory():
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        if os.path.exists(MEMORY_FILE):
            return json.load(open(MEMORY_FILE))
    except:
        pass
    return {
        "lessons":          [],   # lessons from losing trades
        "winning_patterns": [],   # patterns from winning trades
        "macro_mistakes":   [],   # macro misreads
        "sector_notes":     {},   # per-sector lessons
        "banned_setups":    [],   # setups that repeatedly fail
        "last_updated":     None,
    }

def save_memory(memory):
    memory["last_updated"] = datetime.utcnow().isoformat()
    json.dump(memory, open(MEMORY_FILE, "w"), indent=2)

def record_lesson(ticker, action, pnl_pct, reasoning, sell_reason, macro_env, sector):
    """Record a lesson from a closed trade — wins and losses."""
    memory = load_memory()
    entry  = {
        "date":       datetime.utcnow().strftime("%Y-%m-%d"),
        "ticker":     ticker,
        "action":     action,
        "pnl_pct":    pnl_pct,
        "reasoning":  reasoning[:200] if reasoning else "",
        "sell_reason":sell_reason[:200] if sell_reason else "",
        "macro_env":  macro_env,
        "sector":     sector,
    }

    if pnl_pct is not None and pnl_pct < -3:
        # Extract the core lesson
        lesson = {
            **entry,
            "lesson": _extract_lesson(ticker, pnl_pct, reasoning, sell_reason, macro_env),
        }
        memory["lessons"].insert(0, lesson)
        memory["lessons"] = memory["lessons"][:50]  # keep last 50

        # Track macro mistakes
        if macro_env in ["BEARISH", "VOLATILE", "RATE_HIKE_FEAR"]:
            memory["macro_mistakes"].insert(0, {
                "date": entry["date"],
                "mistake": f"Bought {ticker} ({sector}) in {macro_env} environment — lost {pnl_pct:.1f}%",
            })
            memory["macro_mistakes"] = memory["macro_mistakes"][:20]

        # Ban setups that keep failing
        _check_ban_setup(memory, ticker, reasoning, pnl_pct)

    elif pnl_pct is not None and pnl_pct > 5:
        memory["winning_patterns"].insert(0, {
            **entry,
            "pattern": _extract_win_pattern(ticker, pnl_pct, reasoning, macro_env),
        })
        memory["winning_patterns"] = memory["winning_patterns"][:30]

    # Per-sector notes
    if sector and sector not in memory["sector_notes"]:
        memory["sector_notes"][sector] = {"wins": 0, "losses": 0, "notes": []}
    if sector:
        if pnl_pct and pnl_pct > 0:
            memory["sector_notes"][sector]["wins"] += 1
        elif pnl_pct and pnl_pct < 0:
            memory["sector_notes"][sector]["losses"] += 1

    save_memory(memory)

def _extract_lesson(ticker, pnl_pct, reasoning, sell_reason, macro_env):
    reasons = sell_reason or reasoning or ""
    if "jobs" in reasons.lower() or "nfp" in reasons.lower() or "employment" in reasons.lower():
        return f"Do not hold tech/semis going into strong jobs reports — rate hike fears cause sharp selloffs"
    if "fed" in reasons.lower() or "rate" in reasons.lower() or "hike" in reasons.lower():
        return f"Fed hawkish pivot risk is real — reduce tech exposure when rates pricing shifts"
    if "earnings" in reasons.lower() or "miss" in reasons.lower():
        return f"{ticker} sold off on earnings — check earnings dates before entry"
    if macro_env in ["BEARISH", "VOLATILE"]:
        return f"Avoid new longs in {macro_env} macro regime — wait for stabilisation"
    return f"{ticker} lost {pnl_pct:.1f}% — review entry timing and macro context"

def _extract_win_pattern(ticker, pnl_pct, reasoning, macro_env):
    r = reasoning or ""
    if "oversold" in r.lower() or "rsi" in r.lower():
        return f"Oversold RSI entry in {macro_env} worked well — {ticker} +{pnl_pct:.1f}%"
    if "golden cross" in r.lower():
        return f"Golden cross momentum in {macro_env} — {ticker} +{pnl_pct:.1f}%"
    return f"{ticker} +{pnl_pct:.1f}% — {macro_env} regime, {r[:80]}"

def _check_ban_setup(memory, ticker, reasoning, pnl_pct):
    r = (reasoning or "").lower()
    for setup in ["reverse cup", "descending channel", "death cross"]:
        if setup in r:
            already = any(setup in b for b in memory["banned_setups"])
            if not already:
                memory["banned_setups"].append(f"{setup} — repeatedly fails, avoid")

def get_memory_context():
    """Return a concise memory string for the AI decision prompt."""
    memory = load_memory()
    lines  = []

    if memory["lessons"]:
        lines.append("=== LESSONS FROM PAST LOSING TRADES ===")
        for l in memory["lessons"][:8]:
            lines.append(f"- [{l['date']}] {l['ticker']} {l['pnl_pct']:.1f}%: {l['lesson']}")

    if memory["macro_mistakes"]:
        lines.append("\n=== MACRO MISTAKES TO AVOID ===")
        for m in memory["macro_mistakes"][:5]:
            lines.append(f"- {m['mistake']}")

    if memory["winning_patterns"]:
        lines.append("\n=== WHAT HAS WORKED ===")
        for w in memory["winning_patterns"][:5]:
            lines.append(f"- {w['pattern']}")

    if memory["banned_setups"]:
        lines.append("\n=== BANNED SETUPS (proven to fail) ===")
        for b in memory["banned_setups"]:
            lines.append(f"- {b}")

    if memory["sector_notes"]:
        lines.append("\n=== SECTOR TRACK RECORD ===")
        for sector, data in memory["sector_notes"].items():
            if data["wins"] + data["losses"] > 0:
                wr = data["wins"] / (data["wins"] + data["losses"]) * 100
                lines.append(f"- {sector}: {data['wins']}W/{data['losses']}L ({wr:.0f}% win rate)")

    return "\n".join(lines) if lines else "No trade memory yet — first scan."
