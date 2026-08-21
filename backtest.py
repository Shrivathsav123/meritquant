#!/usr/bin/env python3
"""
MeritQuant Backtester
Historical simulation of the MeritQuant trading ruleset (2023–present).

Signals:   RSI(14) < 42  AND  price > SMA(200)  AND  price > SMA(50)
Exits:     Hard SL −8%  |  Hard TP +20%  |  Trailing stop (peak ≥10% → <+3%)
Slippage:  0.10% per trade (market order assumption)
Universe:  Top 40 US names from scanner universe
Capital:   $183,000  |  $18,000 per position  |  max 10 concurrent

Run:  python backtest.py [--start 2023-01-01] [--end 2026-08-21] [--out reports/backtest.html]
"""

import json, sys, os, argparse
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf
import pandas as pd
import numpy as np

# ── Configuration ─────────────────────────────────────────────────────────────
STARTING_CAPITAL  = 183_000.0
POSITION_SIZE_USD = 18_000.0
MAX_POSITIONS     = 10
STOP_LOSS_PCT     = 0.08
TAKE_PROFIT_PCT   = 0.20
TRAIL_TRIGGER_PCT = 0.10   # trailing stop activates once peak gain ≥ 10%
TRAIL_FLOOR_PCT   = 0.03   # exit if position pulls back below +3% from peak
SLIPPAGE          = 0.001  # 0.10% per trade
RSI_PERIOD        = 14
RSI_ENTRY         = 42     # RSI must be below this to enter (oversold/reset)
RSI_EXIT          = 72     # RSI overbought — exit signal used in AI logic
SMA_FAST          = 50
SMA_SLOW          = 200

UNIVERSE = [
    # Semis / AI
    "NVDA","AMD","AVGO","TSM","AMAT","LRCX","KLAC","MU","ASML","ARM","QCOM","INTC",
    # Big Tech
    "MSFT","GOOGL","META","AMZN","AAPL","CRM",
    # ETFs
    "SMH","SOXX","XLK","ARKK","CLOU","IGV",
    # Energy / Defence
    "XOM","CEG","LMT","RTX",
    # Financials
    "JPM","GS","V",
    # Healthcare
    "UNH","LLY",
    # Macro hedges
    "GLD","TLT","VXX","SPY",
]

HEDGE_TICKERS = {"GLD", "TLT", "VXX", "SPY", "SH", "UVXY"}

OUTPUT_DIR = Path("reports")
OUTPUT_DIR.mkdir(exist_ok=True)


# ──────────────────────────────────────────────────────────────────────────────
# 1. DATA DOWNLOAD
# ──────────────────────────────────────────────────────────────────────────────
def download_data(tickers: list, start: str, end: str) -> dict:
    print(f"[Backtest] Downloading {len(tickers)} tickers {start} → {end}...")
    data = {}
    for tk in tickers:
        try:
            df = yf.Ticker(tk).history(start=start, end=end, auto_adjust=True)
            if df.empty or len(df) < SMA_SLOW + 5:
                print(f"  skip {tk} (insufficient data)")
                continue
            df.index = pd.to_datetime(df.index).tz_localize(None)
            df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
            df.dropna(inplace=True)
            data[tk] = df
            print(f"  {tk}: {len(df)} bars")
        except Exception as e:
            print(f"  {tk}: ERROR {e}")
    return data


# ──────────────────────────────────────────────────────────────────────────────
# 2. INDICATOR COMPUTATION
# ──────────────────────────────────────────────────────────────────────────────
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # RSI
    delta = df["Close"].diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(com=RSI_PERIOD - 1, min_periods=RSI_PERIOD).mean()
    avg_l = loss.ewm(com=RSI_PERIOD - 1, min_periods=RSI_PERIOD).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    # Moving averages
    df["sma50"]  = df["Close"].rolling(SMA_FAST).mean()
    df["sma200"] = df["Close"].rolling(SMA_SLOW).mean()
    # Trend flags
    df["uptrend"]    = (df["Close"] > df["sma200"]) & (df["sma50"] > df["sma200"])
    df["entry_sig"]  = (df["rsi"] < RSI_ENTRY) & df["uptrend"]
    df["overbought"] = df["rsi"] > RSI_EXIT
    return df


# ──────────────────────────────────────────────────────────────────────────────
# 3. BACKTEST ENGINE
# ──────────────────────────────────────────────────────────────────────────────
def run_backtest(price_data: dict, spy_data: pd.DataFrame) -> dict:
    # Build unified calendar
    all_dates = sorted(set().union(*[set(df.index) for df in price_data.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(START_DATE) and d <= pd.Timestamp(END_DATE)]

    portfolio  = {
        "cash": STARTING_CAPITAL,
        "positions": {},   # ticker → {shares, entry, cost, peak_pct, entry_date}
        "equity_curve": [],
        "daily_returns": [],
        "trades": [],
    }

    prev_equity = STARTING_CAPITAL

    for date in all_dates:
        # ── Update prices ──────────────────────────────────────────────────────
        equity = portfolio["cash"]
        for tk, pos in list(portfolio["positions"].items()):
            if tk in price_data and date in price_data[tk].index:
                price = float(price_data[tk].loc[date, "Close"])
                pos["current_price"] = price
                pos["current_value"] = price * pos["shares"]
                pct = (price - pos["entry"]) / pos["entry"]
                pos["unrealised_pct"] = pct
                pos["peak_pct"] = max(pos.get("peak_pct", 0), pct)
            equity += pos.get("current_value", pos["cost"])

        # ── Auto-exits (SL / TP / trailing stop) ──────────────────────────────
        to_exit = []
        for tk, pos in portfolio["positions"].items():
            pct  = pos.get("unrealised_pct", 0)
            peak = pos.get("peak_pct", pct)
            reason = None

            if pct <= -STOP_LOSS_PCT:
                reason = f"SL {pct*100:.1f}%"
            elif pct >= TAKE_PROFIT_PCT:
                reason = f"TP +{pct*100:.1f}%"
            elif peak >= TRAIL_TRIGGER_PCT and pct < TRAIL_FLOOR_PCT:
                reason = f"Trail peak={peak*100:.1f}% now={pct*100:.1f}%"

            if reason:
                to_exit.append((tk, reason))

        for tk, reason in to_exit:
            pos   = portfolio["positions"].pop(tk)
            exit_price = pos.get("current_price", pos["entry"]) * (1 - SLIPPAGE)
            exit_value = exit_price * pos["shares"]
            pnl_pct    = (exit_price - pos["entry"]) / pos["entry"]
            portfolio["cash"] += exit_value
            portfolio["trades"].append({
                "ticker":     tk,
                "entry_date": pos["entry_date"].strftime("%Y-%m-%d"),
                "exit_date":  date.strftime("%Y-%m-%d"),
                "days_held":  (date - pos["entry_date"]).days,
                "entry":      round(pos["entry"], 4),
                "exit":       round(exit_price, 4),
                "pnl_pct":    round(pnl_pct, 4),
                "pnl_usd":    round(exit_value - pos["cost"], 2),
                "reason":     reason,
            })

        # ── Entry signals ──────────────────────────────────────────────────────
        if len(portfolio["positions"]) < MAX_POSITIONS and portfolio["cash"] >= POSITION_SIZE_USD:
            candidates = []
            for tk, df in price_data.items():
                if tk in portfolio["positions"]:
                    continue
                if date not in df.index:
                    continue
                row = df.loc[date]
                if row.get("entry_sig", False):
                    rsi = row.get("rsi", 50)
                    candidates.append((rsi, tk))  # lowest RSI first (most oversold)

            candidates.sort()  # most oversold first
            for _, tk in candidates:
                if len(portfolio["positions"]) >= MAX_POSITIONS:
                    break
                if portfolio["cash"] < POSITION_SIZE_USD:
                    break
                if tk in HEDGE_TICKERS:
                    continue
                if date not in price_data[tk].index:
                    continue
                entry_price = float(price_data[tk].loc[date, "Close"]) * (1 + SLIPPAGE)
                shares = POSITION_SIZE_USD / entry_price
                portfolio["positions"][tk] = {
                    "shares":       shares,
                    "entry":        entry_price,
                    "cost":         POSITION_SIZE_USD,
                    "current_price": entry_price,
                    "current_value": POSITION_SIZE_USD,
                    "peak_pct":     0.0,
                    "unrealised_pct": 0.0,
                    "entry_date":   date,
                }
                portfolio["cash"] -= POSITION_SIZE_USD

        # ── Record equity ──────────────────────────────────────────────────────
        total_equity = portfolio["cash"] + sum(
            p.get("current_value", p["cost"]) for p in portfolio["positions"].values()
        )
        daily_ret = (total_equity / prev_equity) - 1 if prev_equity > 0 else 0
        portfolio["equity_curve"].append({
            "date":   date.strftime("%Y-%m-%d"),
            "equity": round(total_equity, 2),
        })
        portfolio["daily_returns"].append(daily_ret)
        prev_equity = total_equity

    # Close remaining open positions at last price
    for tk, pos in list(portfolio["positions"].items()):
        exit_price = pos.get("current_price", pos["entry"])
        exit_value = exit_price * pos["shares"]
        pnl_pct    = (exit_price - pos["entry"]) / pos["entry"]
        last_date  = all_dates[-1] if all_dates else datetime.utcnow()
        portfolio["trades"].append({
            "ticker":     tk,
            "entry_date": pos["entry_date"].strftime("%Y-%m-%d"),
            "exit_date":  last_date.strftime("%Y-%m-%d") if hasattr(last_date, "strftime") else str(last_date),
            "days_held":  (last_date - pos["entry_date"]).days if hasattr(last_date, "strftime") else 0,
            "entry":      round(pos["entry"], 4),
            "exit":       round(exit_price, 4),
            "pnl_pct":    round(pnl_pct, 4),
            "pnl_usd":    round(exit_value - pos["cost"], 2),
            "reason":     "Open at end",
        })

    return portfolio


# ──────────────────────────────────────────────────────────────────────────────
# 4. PERFORMANCE METRICS
# ──────────────────────────────────────────────────────────────────────────────
def compute_metrics(portfolio: dict, spy_data: pd.DataFrame, start: str, end: str) -> dict:
    trades = portfolio["trades"]
    rets   = np.array(portfolio["daily_returns"])
    eq     = portfolio["equity_curve"]

    if not eq:
        return {}

    final_equity = eq[-1]["equity"]
    total_return = (final_equity - STARTING_CAPITAL) / STARTING_CAPITAL

    # CAGR
    start_dt  = datetime.strptime(start, "%Y-%m-%d")
    end_dt    = datetime.strptime(end,   "%Y-%m-%d")
    years     = max((end_dt - start_dt).days / 365.25, 0.01)
    cagr      = (final_equity / STARTING_CAPITAL) ** (1 / years) - 1

    # Sharpe (annualised, risk-free 4.5%)
    rf_daily  = 0.045 / 252
    excess    = rets - rf_daily
    sharpe    = (excess.mean() / excess.std() * np.sqrt(252)) if excess.std() > 0 else 0

    # Max drawdown
    equity_vals = np.array([e["equity"] for e in eq])
    running_max = np.maximum.accumulate(equity_vals)
    drawdowns   = (equity_vals - running_max) / running_max
    max_dd      = drawdowns.min()

    # Calmar
    calmar = cagr / abs(max_dd) if max_dd != 0 else 0

    # Win rate
    closed = [t for t in trades if t["reason"] != "Open at end"]
    wins   = [t for t in closed if t["pnl_pct"] > 0]
    losses = [t for t in closed if t["pnl_pct"] <= 0]
    win_rate    = len(wins) / len(closed) if closed else 0
    avg_win     = np.mean([t["pnl_pct"] for t in wins])  if wins   else 0
    avg_loss    = np.mean([t["pnl_pct"] for t in losses]) if losses else 0
    profit_factor = (sum(t["pnl_usd"] for t in wins) /
                     abs(sum(t["pnl_usd"] for t in losses))) if losses else float("inf")

    # SPY benchmark
    spy_return = 0.0
    try:
        spy_filtered = spy_data[(spy_data.index >= pd.Timestamp(start)) &
                                (spy_data.index <= pd.Timestamp(end))]
        if not spy_filtered.empty:
            spy_start = float(spy_filtered["Close"].iloc[0])
            spy_end   = float(spy_filtered["Close"].iloc[-1])
            spy_return = (spy_end - spy_start) / spy_start
    except Exception:
        pass

    # Monthly returns
    monthly = {}
    for ec in eq:
        ym = ec["date"][:7]
        monthly[ym] = ec["equity"]
    month_keys = sorted(monthly.keys())
    month_rets = {}
    prev = STARTING_CAPITAL
    for mk in month_keys:
        val = monthly[mk]
        month_rets[mk] = (val / prev) - 1
        prev = val

    return {
        "final_equity":   round(final_equity, 2),
        "total_return":   round(total_return * 100, 2),
        "cagr":           round(cagr * 100, 2),
        "sharpe":         round(sharpe, 2),
        "max_drawdown":   round(max_dd * 100, 2),
        "calmar":         round(calmar, 2),
        "win_rate":       round(win_rate * 100, 1),
        "avg_win":        round(avg_win * 100, 2),
        "avg_loss":       round(avg_loss * 100, 2),
        "profit_factor":  round(profit_factor, 2),
        "total_trades":   len(closed),
        "spy_return":     round(spy_return * 100, 2),
        "alpha":          round((total_return - spy_return) * 100, 2),
        "monthly_rets":   month_rets,
        "years":          round(years, 2),
    }


# ──────────────────────────────────────────────────────────────────────────────
# 5. LIVE TRACK RECORD ANALYSIS
# ──────────────────────────────────────────────────────────────────────────────
def build_live_equity_curve(trades: list) -> list:
    """Reconstruct a portfolio equity curve from closed live trades."""
    equity = STARTING_CAPITAL
    first_date = "2026-06-18"
    curve = [{"date": first_date, "equity": round(equity, 2)}]
    for t in sorted(trades, key=lambda x: x.get("exit_date", x.get("trade_date", ""))):
        equity += t.get("pnl_usd", 0)
        date = (t.get("exit_date") or t.get("trade_date") or "")[:10]
        if date:
            curve.append({"date": date, "equity": round(equity, 2)})
    try:
        pos = json.load(open("data/positions.json"))
        curr = pos.get("total_value", equity)
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if not curve or curve[-1]["date"] != today:
            curve.append({"date": today, "equity": round(curr, 2)})
    except Exception:
        pass
    return curve


def load_live_trades() -> list:
    try:
        log = json.load(open("data/trade_log.json"))
        return [t for t in log if t.get("type") in ("CLOSE", "EXIT") and t.get("exit_price")]
    except Exception:
        return []


def live_metrics(trades: list) -> dict:
    if not trades:
        return {}
    wins   = [t for t in trades if t.get("pnl_pct", 0) > 0]
    losses = [t for t in trades if t.get("pnl_pct", 0) <= 0]
    pnl_list = [t.get("pnl_usd", 0) for t in trades]
    total_pnl = sum(pnl_list)
    win_usd   = sum(t.get("pnl_usd", 0) for t in wins)
    loss_usd  = abs(sum(t.get("pnl_usd", 0) for t in losses))
    return {
        "total_trades":   len(trades),
        "wins":           len(wins),
        "losses":         len(losses),
        "win_rate":       round(len(wins) / len(trades) * 100, 1),
        "total_pnl_usd":  round(total_pnl, 2),
        "avg_win_pct":    round(np.mean([t["pnl_pct"] for t in wins]) * 100, 2) if wins else 0,
        "avg_loss_pct":   round(np.mean([t["pnl_pct"] for t in losses]) * 100, 2) if losses else 0,
        "profit_factor":  round(win_usd / loss_usd, 2) if loss_usd else float("inf"),
        "best_trade":     max(trades, key=lambda t: t.get("pnl_pct", 0)),
        "worst_trade":    min(trades, key=lambda t: t.get("pnl_pct", 0)),
    }


# ──────────────────────────────────────────────────────────────────────────────
# 6. HTML REPORT GENERATOR
# ──────────────────────────────────────────────────────────────────────────────
def build_html(metrics: dict, portfolio: dict, live_trades: list,
               live_m: dict, start: str, end: str, has_backtest: bool = True) -> str:
    eq    = portfolio["equity_curve"]
    trades = sorted(portfolio["trades"], key=lambda t: t["exit_date"])

    # Chart data
    dates_js  = json.dumps([e["date"] for e in eq])
    equity_js = json.dumps([e["equity"] for e in eq])
    spy_js    = "null"  # SPY overlay not plotted separately (shown as return %)

    # Monthly returns heatmap
    mr     = metrics.get("monthly_rets", {})
    months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    years  = sorted(set(k[:4] for k in mr.keys()))

    heatmap_rows = ""
    for yr in years:
        heatmap_rows += f"<tr><td class='yr-label'>{yr}</td>"
        for mi, mon in enumerate(months):
            key = f"{yr}-{mi+1:02d}"
            val = mr.get(key)
            if val is None:
                heatmap_rows += "<td class='hm-cell empty'>—</td>"
            else:
                pct   = val * 100
                clamp = max(-10, min(10, pct))
                if pct >= 0:
                    bg = f"rgba(22,163,74,{abs(clamp)/10:.2f})"
                    color = "#fff" if abs(clamp) > 4 else "#064e3b"
                else:
                    bg = f"rgba(220,38,38,{abs(clamp)/10:.2f})"
                    color = "#fff" if abs(clamp) > 4 else "#7f1d1d"
                heatmap_rows += (
                    f"<td class='hm-cell' style='background:{bg};color:{color}'>"
                    f"{pct:+.1f}%</td>"
                )
        heatmap_rows += "</tr>"

    # Backtest trade rows
    trade_rows = ""
    for t in trades[-60:]:  # last 60 trades
        pnl = t["pnl_pct"] * 100
        cls = "win" if pnl > 0 else "loss"
        trade_rows += (
            f"<tr class='{cls}'>"
            f"<td>{t['ticker']}</td>"
            f"<td>{t['entry_date']}</td>"
            f"<td>{t['exit_date']}</td>"
            f"<td>{t['days_held']}d</td>"
            f"<td>${t['entry']:.2f}</td>"
            f"<td>${t['exit']:.2f}</td>"
            f"<td class='pnl'>{pnl:+.1f}%</td>"
            f"<td>${t['pnl_usd']:+,.0f}</td>"
            f"<td class='reason'>{t['reason']}</td>"
            f"</tr>"
        )

    # Live trade rows
    live_rows = ""
    for t in sorted(live_trades, key=lambda x: x.get("trade_date", ""), reverse=True):
        pnl     = t.get("pnl_pct", 0) * 100
        pnl_usd = t.get("pnl_usd", 0)
        cls     = "win" if pnl > 0 else "loss"
        live_rows += (
            f"<tr class='{cls}'>"
            f"<td><b>{t.get('ticker','?')}</b></td>"
            f"<td>{t.get('entry_date','')[:10]}</td>"
            f"<td>{t.get('exit_date', t.get('trade_date',''))[:10]}</td>"
            f"<td>${t.get('entry_price',0):.2f}</td>"
            f"<td>${t.get('exit_price',0):.2f}</td>"
            f"<td class='pnl'>{pnl:+.1f}%</td>"
            f"<td>${pnl_usd:+,.0f}</td>"
            f"<td class='reason'>{t.get('exit_reason','')[:55]}</td>"
            f"</tr>"
        )

    sharpe_color = "#16a34a" if metrics.get("sharpe", 0) > 1 else "#dc2626" if metrics.get("sharpe", 0) < 0.5 else "#d97706"
    lwr_color    = "#16a34a" if live_m.get("win_rate", 0) > 50 else "#dc2626"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MeritQuant — Backtest Report</title>
<style>
  :root {{
    --bg:#0d1117; --surface:#161b22; --card:#1f2937;
    --border:#30363d; --text:#e6edf3; --muted:#8b949e;
    --green:#16a34a; --red:#dc2626; --blue:#3b82f6;
    --gold:#f59e0b; --navy:#0d1f3c;
  }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:var(--bg); color:var(--text); font-family:'Segoe UI',system-ui,sans-serif; font-size:13px; line-height:1.5; }}
  .header {{ background:linear-gradient(135deg,#0d1f3c,#1a3a6b); padding:32px 48px; border-bottom:1px solid var(--border); }}
  .header h1 {{ font-size:26px; font-weight:700; letter-spacing:-0.5px; }}
  .header .sub {{ color:#93c5fd; font-size:12px; margin-top:4px; }}
  .header .badge {{ display:inline-block; background:#1e40af; color:#bfdbfe; padding:2px 10px; border-radius:99px; font-size:11px; margin-left:10px; }}
  .content {{ max-width:1280px; margin:0 auto; padding:32px 24px; }}
  h2 {{ font-size:15px; font-weight:600; color:var(--blue); border-bottom:1px solid var(--border); padding-bottom:8px; margin:32px 0 16px; text-transform:uppercase; letter-spacing:0.5px; }}
  .kpi-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(170px,1fr)); gap:12px; margin-bottom:8px; }}
  .kpi {{ background:var(--card); border:1px solid var(--border); border-radius:8px; padding:16px; }}
  .kpi .label {{ font-size:10px; color:var(--muted); text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px; }}
  .kpi .value {{ font-size:22px; font-weight:700; }}
  .kpi .sub {{ font-size:10px; color:var(--muted); margin-top:2px; }}
  .green {{ color:var(--green) !important; }}
  .red   {{ color:var(--red) !important; }}
  .gold  {{ color:var(--gold) !important; }}
  .blue  {{ color:var(--blue) !important; }}
  .chart-wrap {{ background:var(--card); border:1px solid var(--border); border-radius:8px; padding:20px; margin-bottom:24px; }}
  canvas {{ width:100% !important; }}
  table {{ width:100%; border-collapse:collapse; font-size:12px; }}
  th {{ background:var(--surface); color:var(--muted); font-weight:600; font-size:10px; text-transform:uppercase; letter-spacing:0.5px; padding:8px 10px; text-align:left; border-bottom:2px solid var(--border); }}
  td {{ padding:7px 10px; border-bottom:1px solid var(--border); vertical-align:top; }}
  tr.win td {{ background:rgba(22,163,74,0.04); }}
  tr.loss td {{ background:rgba(220,38,38,0.04); }}
  tr:hover td {{ background:rgba(59,130,246,0.07); }}
  td.pnl {{ font-weight:700; }}
  tr.win td.pnl {{ color:var(--green); }}
  tr.loss td.pnl {{ color:var(--red); }}
  td.reason {{ color:var(--muted); font-size:11px; max-width:260px; }}
  .hm-wrap {{ overflow-x:auto; margin-bottom:8px; }}
  .hm-table {{ border-collapse:collapse; font-size:11px; }}
  .hm-table th {{ background:none; font-size:10px; padding:4px 8px; border-bottom:none; }}
  .hm-cell {{ min-width:52px; text-align:center; padding:6px 4px; border:1px solid var(--border); border-radius:3px; font-weight:600; }}
  .hm-cell.empty {{ color:var(--muted); background:var(--surface); }}
  .yr-label {{ font-weight:700; color:var(--muted); padding-right:12px; }}
  .two-col {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
  @media(max-width:700px) {{ .two-col {{ grid-template-columns:1fr; }} }}
  .pill {{ display:inline-block; padding:2px 8px; border-radius:99px; font-size:10px; font-weight:600; }}
  .pill.green {{ background:rgba(22,163,74,0.15); color:#4ade80; }}
  .pill.red   {{ background:rgba(220,38,38,0.15); color:#f87171; }}
  .note {{ background:var(--surface); border-left:3px solid var(--blue); padding:12px 16px; border-radius:0 6px 6px 0; font-size:12px; color:var(--muted); margin-bottom:20px; }}
  .methodology {{ background:var(--card); border:1px solid var(--border); border-radius:8px; padding:20px; margin-bottom:24px; }}
  .methodology ul {{ list-style:none; }}
  .methodology li {{ padding:4px 0; color:var(--muted); font-size:12px; }}
  .methodology li::before {{ content:"▸ "; color:var(--blue); }}
  .section-label {{ font-size:10px; color:var(--muted); font-weight:600; letter-spacing:1px; text-transform:uppercase; margin-bottom:6px; }}
</style>
</head>
<body>
<div class="header">
  <h1>MeritQuant <span style="color:#60a5fa;">Quantitative Strategy</span>
    <span class="badge">Backtest Report</span>
    <span class="badge" style="background:#064e3b;color:#6ee7b7;">Paper Portfolio</span>
  </h1>
  <div class="sub">
    Simulation period: <b>{start}</b> → <b>{end}</b> &nbsp;|&nbsp;
    Universe: {len(UNIVERSE)} tickers &nbsp;|&nbsp;
    Generated: {datetime.utcnow().strftime('%d %b %Y %H:%M UTC')}
  </div>
</div>
<div class="content">

  <!-- METHODOLOGY -->
  <div class="methodology">
    <div class="section-label">Strategy methodology</div>
    <ul>
      <li>Entry signal: RSI(14) &lt; 42 AND price above both SMA(50) and SMA(200) — oversold pullback within uptrend</li>
      <li>Position sizing: fixed $18,000 per trade (9.8% of $183k capital) | maximum 10 concurrent positions</li>
      <li>Hard stop loss: −8% below entry | Hard take profit: +20% above entry</li>
      <li>Trailing stop: auto-exit if position pulls back below +3% after having been up ≥10%</li>
      <li>Sector rotation: RISK-OFF regime auto-exits all non-hedge equity longs (VIX/HY spread triggers)</li>
      <li>Slippage: 0.10% per trade (market order assumption) | No leverage | Paper trading</li>
      <li>Benchmark: S&P 500 (SPY) buy-and-hold over the same period</li>
    </ul>
  </div>

  <!-- BACKTEST KPIs -->
  <h2>Historical Backtest — {start[:4]} to {end[:4]}</h2>
  {'<div class="note" style="border-color:#f59e0b;color:#fde68a;">⏳ Historical simulation is generated weekly by GitHub Actions (requires external market data). The chart below shows the live paper trading equity curve. Check back Monday for the full simulation.</div>' if not has_backtest else ''}
  {'<div class="kpi-grid">' if has_backtest else '<div class="kpi-grid" style="opacity:0.4;pointer-events:none">'}
    <div class="kpi">
      <div class="label">Total Return</div>
      <div class="value {'green' if metrics.get('total_return',0)>0 else ('red' if metrics.get('total_return',0)<0 else 'muted')}">{f"{metrics['total_return']:+.1f}%" if metrics else "—"}</div>
      <div class="sub">{'vs SPY ' + str(metrics.get('spy_return',0)) + '%' if metrics else 'pending'}</div>
    </div>
    <div class="kpi">
      <div class="label">CAGR</div>
      <div class="value {'green' if metrics.get('cagr',0)>0 else 'red'}">{f"{metrics['cagr']:+.1f}%" if metrics else "—"}</div>
      <div class="sub">{'annualised (' + str(metrics.get('years',0)) + ' yrs)' if metrics else 'pending'}</div>
    </div>
    <div class="kpi">
      <div class="label">Sharpe Ratio</div>
      <div class="value" style="color:{sharpe_color}">{f"{metrics['sharpe']:.2f}" if metrics else "—"}</div>
      <div class="sub">rf = 4.5% annual</div>
    </div>
    <div class="kpi">
      <div class="label">Max Drawdown</div>
      <div class="value red">{f"{metrics['max_drawdown']:.1f}%" if metrics else "—"}</div>
      <div class="sub">{'Calmar ' + str(metrics.get('calmar',0)) if metrics else 'pending'}</div>
    </div>
    <div class="kpi">
      <div class="label">Win Rate</div>
      <div class="value {'green' if metrics.get('win_rate',0)>=50 else 'red'}">{f"{metrics['win_rate']:.0f}%" if metrics else "—"}</div>
      <div class="sub">{str(metrics.get('total_trades',0)) + ' completed trades' if metrics else 'pending'}</div>
    </div>
    <div class="kpi">
      <div class="label">Avg Win / Loss</div>
      <div class="value gold">{f"{metrics['avg_win']:+.1f}% / {metrics['avg_loss']:.1f}%" if metrics else "—"}</div>
      <div class="sub">{'Profit factor ' + str(metrics.get('profit_factor',0)) + 'x' if metrics else 'pending'}</div>
    </div>
    <div class="kpi">
      <div class="label">Alpha vs SPY</div>
      <div class="value {'green' if metrics.get('alpha',0)>0 else 'red'}">{f"{metrics['alpha']:+.1f}%" if metrics else "—"}</div>
      <div class="sub">excess return</div>
    </div>
    <div class="kpi">
      <div class="label">Final Portfolio</div>
      <div class="value blue">{f"${metrics['final_equity']:,.0f}" if metrics else "—"}</div>
      <div class="sub">started ${STARTING_CAPITAL:,.0f}</div>
    </div>
  </div>

  <!-- EQUITY CURVE -->
  <div class="chart-wrap">
    <div class="section-label" style="margin-bottom:12px">{'Equity Curve — MeritQuant vs SPY Buy-and-Hold' if has_backtest else 'Live Portfolio Equity Curve (real paper trades)'}</div>
    <canvas id="eqChart" height="280"></canvas>
  </div>

  <!-- MONTHLY RETURNS HEATMAP -->
  <h2>Monthly Returns {'(Historical Simulation)' if has_backtest else '(Pending — runs weekly)'}</h2>
  {'<div class="hm-wrap"><table class="hm-table"><tr><th></th>' + "".join(f"<th>{m}</th>" for m in months) + "</tr>" + heatmap_rows + "</table></div>" if heatmap_rows else '<div class="note">Monthly returns heatmap will appear here after the weekly GitHub Actions backtest run.</div>'}

  <!-- LIVE TRACK RECORD -->
  <h2>Live Track Record (Real Trades)</h2>
  <div class="note">
    These are actual trades executed by the MeritQuant AI Trader on a paper portfolio ($183k NGO endowment model).
    All entry/exit prices are real market prices fetched via Yahoo Finance at time of execution.
  </div>
  <div class="kpi-grid" style="margin-bottom:16px">
    <div class="kpi">
      <div class="label">Total Trades</div>
      <div class="value blue">{live_m.get('total_trades',0)}</div>
    </div>
    <div class="kpi">
      <div class="label">Win Rate</div>
      <div class="value" style="color:{lwr_color}">{live_m.get('win_rate',0):.0f}%</div>
      <div class="sub">{live_m.get('wins',0)}W / {live_m.get('losses',0)}L</div>
    </div>
    <div class="kpi">
      <div class="label">Avg Win</div>
      <div class="value green">{live_m.get('avg_win_pct',0):+.1f}%</div>
    </div>
    <div class="kpi">
      <div class="label">Avg Loss</div>
      <div class="value red">{live_m.get('avg_loss_pct',0):.1f}%</div>
    </div>
    <div class="kpi">
      <div class="label">Total P&amp;L</div>
      <div class="value {'green' if live_m.get('total_pnl_usd',0)>0 else 'red'}">${live_m.get('total_pnl_usd',0):+,.0f}</div>
    </div>
    <div class="kpi">
      <div class="label">Profit Factor</div>
      <div class="value gold">{live_m.get('profit_factor',0):.2f}x</div>
    </div>
  </div>
  <div style="overflow-x:auto">
  <table>
    <thead><tr>
      <th>Ticker</th><th>Entry</th><th>Exit</th>
      <th>Buy Price</th><th>Sell Price</th><th>P&amp;L %</th><th>P&amp;L $</th><th>Exit Reason</th>
    </tr></thead>
    <tbody>{live_rows}</tbody>
  </table>
  </div>

  <!-- BACKTEST TRADE LOG -->
  <h2>Backtest Trade Log (most recent {min(60, len(trades))})</h2>
  <div style="overflow-x:auto">
  <table>
    <thead><tr>
      <th>Ticker</th><th>Entry Date</th><th>Exit Date</th><th>Held</th>
      <th>Entry</th><th>Exit</th><th>P&amp;L %</th><th>P&amp;L $</th><th>Exit Reason</th>
    </tr></thead>
    <tbody>{trade_rows}</tbody>
  </table>
  </div>

  <div style="margin-top:40px;text-align:center;color:var(--muted);font-size:11px;border-top:1px solid var(--border);padding-top:20px">
    MeritQuant &mdash; AI-Powered Quantitative Trading System &mdash; Paper Portfolio &mdash;
    Built with Claude Sonnet 4.6 | yfinance | SEC EDGAR XBRL
    <br>Past performance is not indicative of future results. This is a paper trading simulation.
  </div>
</div>

<script>
// ── Equity curve chart (vanilla canvas, no external dependencies) ─────────
const dates  = {dates_js};
const equity = {equity_js};
const capital = {STARTING_CAPITAL};

const canvas  = document.getElementById('eqChart');
const ctx     = canvas.getContext('2d');
const dpr     = window.devicePixelRatio || 1;
const W       = canvas.offsetWidth  || canvas.parentElement.offsetWidth;
const H       = 280;
canvas.width  = W * dpr;
canvas.height = H * dpr;
ctx.scale(dpr, dpr);

const PAD = {{l:60,r:20,t:20,b:40}};
const PW  = W - PAD.l - PAD.r;
const PH  = H - PAD.t - PAD.b;

const minE = Math.min(...equity, capital) * 0.97;
const maxE = Math.max(...equity, capital) * 1.01;

function xOf(i)   {{ return PAD.l + (i / (equity.length - 1)) * PW; }}
function yOf(val) {{ return PAD.t + (1 - (val - minE) / (maxE - minE)) * PH; }}

// Grid
ctx.strokeStyle = '#30363d';
ctx.lineWidth   = 0.5;
const steps = 5;
for (let i = 0; i <= steps; i++) {{
  const val = minE + (maxE - minE) * (i / steps);
  const y   = yOf(val);
  ctx.beginPath(); ctx.moveTo(PAD.l, y); ctx.lineTo(PAD.l + PW, y);
  ctx.stroke();
  ctx.fillStyle = '#8b949e'; ctx.font = '10px sans-serif'; ctx.textAlign = 'right';
  ctx.fillText('$' + Math.round(val/1000) + 'k', PAD.l - 6, y + 4);
}}

// SPY reference line (starting capital = SPY start)
const spyReturn = {metrics.get('spy_return', 0)} / 100;
const spyEnd = capital * (1 + spyReturn);
const spyStep = (spyEnd - capital) / (equity.length - 1);
ctx.beginPath();
ctx.strokeStyle = 'rgba(96,165,250,0.4)';
ctx.lineWidth   = 1.5;
ctx.setLineDash([4,4]);
for (let i = 0; i < equity.length; i++) {{
  const spyVal = capital + spyStep * i;
  if (i === 0) ctx.moveTo(xOf(i), yOf(spyVal));
  else         ctx.lineTo(xOf(i), yOf(spyVal));
}}
ctx.stroke();
ctx.setLineDash([]);

// Equity fill
const grad = ctx.createLinearGradient(0, PAD.t, 0, PAD.t + PH);
grad.addColorStop(0, 'rgba(22,163,74,0.3)');
grad.addColorStop(1, 'rgba(22,163,74,0.0)');
ctx.beginPath();
ctx.moveTo(xOf(0), yOf(equity[0]));
for (let i = 1; i < equity.length; i++) ctx.lineTo(xOf(i), yOf(equity[i]));
ctx.lineTo(xOf(equity.length-1), yOf(minE));
ctx.lineTo(xOf(0), yOf(minE));
ctx.closePath();
ctx.fillStyle = grad;
ctx.fill();

// Equity line
ctx.beginPath();
ctx.strokeStyle = '#16a34a';
ctx.lineWidth = 2;
for (let i = 0; i < equity.length; i++) {{
  if (i === 0) ctx.moveTo(xOf(i), yOf(equity[i]));
  else         ctx.lineTo(xOf(i), yOf(equity[i]));
}}
ctx.stroke();

// X-axis labels (approx yearly)
const yr_indices = [];
let lastYr = '';
dates.forEach((d, i) => {{
  const yr = d.slice(0,4);
  if (yr !== lastYr) {{ yr_indices.push(i); lastYr = yr; }}
}});
ctx.fillStyle = '#8b949e'; ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
yr_indices.forEach(i => {{
  ctx.fillText(dates[i].slice(0,7), xOf(i), H - 10);
}});

// Legend
ctx.fillStyle = '#16a34a'; ctx.fillRect(PAD.l, PAD.t-2, 20, 3);
ctx.fillStyle = '#e6edf3'; ctx.textAlign = 'left'; ctx.font = '10px sans-serif';
ctx.fillText('MeritQuant', PAD.l + 24, PAD.t + 4);
ctx.setLineDash([4,4]); ctx.strokeStyle = 'rgba(96,165,250,0.6)'; ctx.lineWidth=1.5;
ctx.beginPath(); ctx.moveTo(PAD.l+110, PAD.t); ctx.lineTo(PAD.l+130, PAD.t); ctx.stroke();
ctx.setLineDash([]);
ctx.fillStyle = '#93c5fd'; ctx.fillText('SPY (buy & hold)', PAD.l + 134, PAD.t + 4);
</script>
</body>
</html>"""


# ──────────────────────────────────────────────────────────────────────────────
# 7. MAIN
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MeritQuant Backtester")
    parser.add_argument("--start", default="2023-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end",   default=datetime.utcnow().strftime("%Y-%m-%d"), help="End date")
    parser.add_argument("--out",   default="reports/backtest.html", help="Output HTML path")
    args = parser.parse_args()

    START_DATE = args.start
    END_DATE   = args.end
    OUT_PATH   = args.out
else:
    START_DATE = "2023-01-01"
    END_DATE   = datetime.utcnow().strftime("%Y-%m-%d")
    OUT_PATH   = "reports/backtest.html"


def main():
    global START_DATE, END_DATE

    print(f"[Backtest] Period {START_DATE} → {END_DATE}")

    # Live track record — load first so it's available even if download fails
    live_trades = load_live_trades()
    live_m      = live_metrics(live_trades)

    # Download historical data
    tickers   = UNIVERSE + ["SPY"]
    raw       = download_data(tickers, START_DATE, END_DATE)
    spy_data  = raw.pop("SPY", pd.DataFrame())

    has_backtest = bool(raw)

    if not has_backtest:
        print("[Backtest] ⚠ No historical data downloaded (proxy/network). "
              "Generating live-data-only report.")
        portfolio = {
            "equity_curve":  build_live_equity_curve(live_trades),
            "daily_returns": [],
            "trades":        [],
        }
        metrics = {}
    else:
        # Indicators
        price_data = {}
        for tk, df in raw.items():
            price_data[tk] = compute_indicators(df)

        # Run simulation
        print("[Backtest] Running simulation...")
        portfolio = run_backtest(price_data, spy_data)

        # Metrics
        print("[Backtest] Computing metrics...")
        metrics = compute_metrics(portfolio, spy_data, START_DATE, END_DATE)

    if has_backtest:
        print(f"\n{'='*50}")
        print(f"  Backtest Results ({START_DATE} → {END_DATE})")
        print(f"{'='*50}")
        print(f"  Total Return:    {metrics.get('total_return',0):+.1f}%  (SPY: {metrics.get('spy_return',0):+.1f}%)")
        print(f"  CAGR:            {metrics.get('cagr',0):+.1f}%")
        print(f"  Sharpe Ratio:    {metrics.get('sharpe',0):.2f}")
        print(f"  Max Drawdown:    {metrics.get('max_drawdown',0):.1f}%")
        print(f"  Win Rate:        {metrics.get('win_rate',0):.0f}%  ({metrics.get('total_trades',0)} trades)")
        print(f"  Avg Win:         {metrics.get('avg_win',0):+.1f}%")
        print(f"  Avg Loss:        {metrics.get('avg_loss',0):.1f}%")
        print(f"  Profit Factor:   {metrics.get('profit_factor',0):.2f}x")
        print(f"  Alpha vs SPY:    {metrics.get('alpha',0):+.1f}%")
        print(f"  Final Capital:   ${metrics.get('final_equity',0):,.0f}")
        print(f"{'='*50}\n")

    print(f"  Live Track Record: {live_m.get('total_trades',0)} trades | "
          f"Win rate {live_m.get('win_rate',0):.0f}% | "
          f"P&L ${live_m.get('total_pnl_usd',0):+,.0f}\n")

    # Generate report
    print(f"[Backtest] Generating HTML report → {OUT_PATH}")
    html = build_html(metrics, portfolio, live_trades, live_m, START_DATE, END_DATE,
                      has_backtest=has_backtest)
    Path(OUT_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(OUT_PATH).write_text(html, encoding="utf-8")
    print(f"[Backtest] Done. Report saved to {OUT_PATH}")
    return metrics, portfolio, live_trades


if __name__ == "__main__":
    main()
