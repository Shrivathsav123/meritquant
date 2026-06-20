#!/usr/bin/env python3
"""
MeritQuant — Email Alert System
Sends trade decisions, PDF reports, and session summaries to shrifx333@gmail.com
Uses Gmail SMTP with App Password (set EMAIL_PASS in GitHub secrets)
"""

import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timezone

log = logging.getLogger("email_alerts")

EMAIL_FROM = os.environ.get("EMAIL_USER", "shrifx333@gmail.com")
EMAIL_TO   = "shrifx333@gmail.com"
EMAIL_PASS = os.environ.get("EMAIL_PASS", "")

SMTP_HOST  = "smtp.gmail.com"
SMTP_PORT  = 587


def _send(subject: str, html_body: str, attachments: list = []) -> bool:
    """Core send function. attachments = list of (filename, bytes)"""
    if not EMAIL_PASS:
        log.warning("EMAIL_PASS not set — skipping email.")
        return False
    try:
        msg = MIMEMultipart("mixed")
        msg["From"]    = f"MeritQuant Bot <{EMAIL_FROM}>"
        msg["To"]      = EMAIL_TO
        msg["Subject"] = subject

        msg.attach(MIMEText(html_body, "html"))

        for filename, file_bytes in attachments:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(file_bytes)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
            msg.attach(part)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(EMAIL_FROM, EMAIL_PASS)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())

        log.info(f"Email sent: {subject}")
        return True

    except Exception as e:
        log.error(f"Email failed: {e}")
        return False


def send_trade_alert(action: dict, portfolio: dict, macro: dict) -> bool:
    """Send email for every ENTER or EXIT decision with full reasoning."""
    act     = action.get("action", "")
    ticker  = action.get("ticker", "")
    conv    = action.get("conviction", "—")
    size    = action.get("position_size_usd", 0)
    thesis  = action.get("thesis", "—")
    catalyst= action.get("catalyst", "—")
    setup   = action.get("technical_setup", "—")
    risk    = action.get("risk_factors", "—")
    tier    = action.get("tier", "—")

    ind     = macro.get("indicators", {})
    vix     = ind.get("VIX", {}).get("value")
    curve   = ind.get("T10Y2Y", {}).get("value")
    hy      = ind.get("HY_SPREAD", {}).get("value")
    regime  = macro.get("regime", "NEUTRAL")
    port_val= portfolio.get("total_value", 0)
    cash    = portfolio.get("cash", 0)
    pos_cnt = len(portfolio.get("positions", []))

    colour  = "#1a6e3e" if act == "ENTER" else "#9e2020"
    emoji   = "🟢" if act == "ENTER" else "🔴"
    now_str = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    window  = "OPEN 9:35 AM ET" if datetime.now(timezone.utc).hour < 19 else "PRE-CLOSE 3:30 PM ET"

    html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body{{font-family:Arial,sans-serif;background:#f4f6fa;margin:0;padding:20px}}
  .card{{background:#fff;border-radius:12px;max-width:600px;margin:0 auto;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.08)}}
  .header{{background:#0d1f3c;padding:24px 28px;color:#fff}}
  .header h1{{margin:0;font-size:22px;font-weight:800;letter-spacing:-0.5px}}
  .header p{{margin:6px 0 0;font-size:12px;color:#a0b8d4;letter-spacing:1px}}
  .action-bar{{background:{colour};padding:16px 28px;display:flex;align-items:center;gap:12px}}
  .action-bar .act{{font-size:28px;font-weight:900;color:#fff;letter-spacing:-1px}}
  .action-bar .meta{{color:rgba(255,255,255,0.85);font-size:13px}}
  .body{{padding:24px 28px}}
  .row{{display:flex;gap:12px;margin-bottom:12px}}
  .box{{flex:1;background:#f8faff;border:1px solid #dde8f5;border-radius:8px;padding:12px}}
  .box label{{font-size:10px;letter-spacing:2px;color:#7a9cc4;text-transform:uppercase;display:block;margin-bottom:4px;font-weight:700}}
  .box span{{font-size:15px;font-weight:700;color:#0d1f3c}}
  .section{{margin-bottom:16px}}
  .section h3{{font-size:10px;letter-spacing:2px;color:#7a9cc4;text-transform:uppercase;font-weight:700;margin:0 0 6px}}
  .section p{{margin:0;font-size:13px;color:#2c3e50;line-height:1.7;background:#f8faff;border-left:3px solid #2a6db5;padding:10px 14px;border-radius:0 6px 6px 0}}
  .macro-strip{{background:#0d1f3c;padding:14px 28px;display:flex;gap:20px}}
  .mc{{color:#a0b8d4;font-size:11px}}
  .mc span{{display:block;color:#fff;font-weight:700;font-size:13px;margin-top:2px;font-family:monospace}}
  .footer{{background:#f4f6fa;padding:14px 28px;font-size:11px;color:#999;text-align:center;border-top:1px solid #eee}}
</style>
</head>
<body>
<div class="card">
  <div class="header">
    <h1>MeritQuant AI Trade Decision</h1>
    <p>{window} &nbsp;·&nbsp; {now_str}</p>
  </div>
  <div class="action-bar">
    <div class="act">{emoji} {act} ${ticker}</div>
    <div class="meta">Conviction {conv}/10 &nbsp;|&nbsp; Tier {tier} &nbsp;|&nbsp; ${size:,.0f}</div>
  </div>
  <div class="body">
    <div class="row">
      <div class="box"><label>Portfolio Value</label><span>${port_val:,.0f}</span></div>
      <div class="box"><label>Cash Available</label><span>${cash:,.0f}</span></div>
      <div class="box"><label>Open Positions</label><span>{pos_cnt}/6</span></div>
    </div>
    <div class="section">
      <h3>Investment Thesis</h3>
      <p>{thesis}</p>
    </div>
    <div class="section">
      <h3>Primary Catalyst</h3>
      <p>{catalyst}</p>
    </div>
    <div class="section">
      <h3>Technical Setup</h3>
      <p>{setup}</p>
    </div>
    <div class="section">
      <h3>Key Risk Factors</h3>
      <p>{risk}</p>
    </div>
  </div>
  <div class="macro-strip">
    <div class="mc">Regime<span>{regime}</span></div>
    <div class="mc">VIX<span>{f"{vix:.1f}" if vix else "—"}</span></div>
    <div class="mc">Yield Curve<span>{f"{curve:+.2f}%" if curve else "—"}</span></div>
    <div class="mc">HY Spread<span>{f"{hy:.0f}bps" if hy else "—"}</span></div>
  </div>
  <div class="footer">
    MeritQuant Autonomous Trader &nbsp;·&nbsp; Paper Portfolio &nbsp;·&nbsp; Not financial advice
  </div>
</div>
</body>
</html>
"""

    subject = f"MeritQuant {emoji} {act} ${ticker} — Conviction {conv}/10 — {now_str}"
    return _send(subject, html)


def send_session_summary(decision: dict, portfolio: dict, macro: dict,
                          actions: list, auto_exits: list,
                          pdf_bytes: bytes = None, xlsx_bytes: bytes = None) -> bool:
    """Send full session summary with PDF and Excel attached."""
    regime  = decision.get("regime", "NEUTRAL")
    assess  = decision.get("market_assessment", "—")
    memory  = decision.get("memory_applied", "—")
    notes   = decision.get("portfolio_notes", "—")
    port_val= portfolio.get("total_value", 0)
    cash    = portfolio.get("cash", 0)
    pnl     = port_val - 183_000
    pnl_pct = (pnl / 183_000) * 100
    now_str = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    window  = "OPEN 9:35 AM ET" if datetime.now(timezone.utc).hour < 19 else "PRE-CLOSE 3:30 PM ET"

    regime_colour = {"RISK-ON": "#1a6e3e", "RISK-OFF": "#9e2020",
                     "CAUTION": "#d07a10"}.get(regime, "#1a4a88")

    actions_html = ""
    for a in actions:
        e = "🟢" if a["action"] == "ENTER" else "🔴"
        actions_html += f"""
        <tr>
          <td style="padding:8px 12px;font-weight:700">{e} {a['action']}</td>
          <td style="padding:8px 12px;font-family:monospace;font-weight:700">${a['ticker']}</td>
          <td style="padding:8px 12px">${a.get('position_size_usd',0):,.0f}</td>
          <td style="padding:8px 12px">{a.get('conviction',0)}/10</td>
          <td style="padding:8px 12px;font-size:12px;color:#444">{a.get('thesis','')[:80]}...</td>
        </tr>"""

    exits_html = ""
    for ticker, reason, pnl_p in auto_exits:
        e = "✅" if pnl_p > 0 else "🛑"
        exits_html += f"<tr><td style='padding:8px 12px'>{e} {ticker}</td><td style='padding:8px 12px'>{reason}</td><td style='padding:8px 12px;font-weight:700;color:{'#1a6e3e' if pnl_p>0 else '#9e2020'}'>{pnl_p*100:+.1f}%</td></tr>"

    positions_html = ""
    for p in portfolio.get("positions", []):
        pp = p.get("unrealised_pct", 0)
        col = "#1a6e3e" if pp >= 0 else "#9e2020"
        positions_html += f"""
        <tr>
          <td style="padding:8px 12px;font-weight:700;font-family:monospace">${p['ticker']}</td>
          <td style="padding:8px 12px">{p['shares']:.1f} shares @ ${p['entry_price']:.2f}</td>
          <td style="padding:8px 12px;font-weight:700;color:{col}">{pp*100:+.1f}%</td>
          <td style="padding:8px 12px;font-size:12px;color:#444">{p.get('thesis_summary','')[:60]}...</td>
        </tr>"""

    pnl_colour = "#1a6e3e" if pnl >= 0 else "#9e2020"

    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8">
<style>
  body{{font-family:Arial,sans-serif;background:#f4f6fa;margin:0;padding:20px}}
  .card{{background:#fff;border-radius:12px;max-width:680px;margin:0 auto;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.08)}}
  .header{{background:#0d1f3c;padding:24px 28px;color:#fff}}
  .header h1{{margin:0;font-size:20px;font-weight:800}}
  .header p{{margin:4px 0 0;font-size:11px;color:#a0b8d4;letter-spacing:1px}}
  .stats{{display:flex;border-bottom:1px solid #eee}}
  .stat{{flex:1;padding:16px 20px;text-align:center;border-right:1px solid #eee}}
  .stat:last-child{{border-right:none}}
  .stat label{{font-size:9px;letter-spacing:2px;color:#7a9cc4;text-transform:uppercase;display:block;margin-bottom:4px;font-weight:700}}
  .stat span{{font-size:18px;font-weight:800;color:#0d1f3c}}
  .body{{padding:20px 28px}}
  .section{{margin-bottom:18px}}
  .section h3{{font-size:10px;letter-spacing:2px;color:#7a9cc4;text-transform:uppercase;font-weight:700;margin:0 0 8px;padding-bottom:6px;border-bottom:1px solid #eee}}
  .regime-tag{{display:inline-block;padding:4px 14px;border-radius:20px;font-size:12px;font-weight:700;color:#fff;background:{regime_colour};margin-bottom:10px}}
  table{{width:100%;border-collapse:collapse;font-size:12px}}
  th{{background:#f0f4f9;padding:8px 12px;text-align:left;font-size:10px;letter-spacing:1px;color:#7a9cc4;text-transform:uppercase;font-weight:700}}
  tr:nth-child(even){{background:#fafbff}}
  .footer{{background:#f4f6fa;padding:14px 28px;font-size:11px;color:#999;text-align:center;border-top:1px solid #eee}}
</style>
</head>
<body>
<div class="card">
  <div class="header">
    <h1>MeritQuant — Session Summary</h1>
    <p>{window} &nbsp;·&nbsp; {now_str} &nbsp;·&nbsp; {len(actions)} actions &nbsp;·&nbsp; {len(auto_exits)} auto-exits</p>
  </div>
  <div class="stats">
    <div class="stat"><label>Portfolio</label><span>${port_val:,.0f}</span></div>
    <div class="stat"><label>Net P&L</label><span style="color:{pnl_colour}">{pnl:+,.0f}</span></div>
    <div class="stat"><label>Return</label><span style="color:{pnl_colour}">{pnl_pct:+.2f}%</span></div>
    <div class="stat"><label>Cash</label><span>${cash:,.0f}</span></div>
    <div class="stat"><label>Positions</label><span>{len(portfolio.get('positions',[]))}/6</span></div>
  </div>
  <div class="body">
    <div class="section">
      <h3>Market Assessment</h3>
      <div class="regime-tag">{regime}</div>
      <p style="margin:0;font-size:13px;color:#2c3e50;line-height:1.7">{assess}</p>
    </div>
    {"<div class='section'><h3>Trade Decisions</h3><table><tr><th>Action</th><th>Ticker</th><th>Size</th><th>Conviction</th><th>Thesis</th></tr>" + actions_html + "</table></div>" if actions else "<div class='section'><h3>Trade Decisions</h3><p style='color:#999;font-size:13px'>No new positions this session — macro or conviction threshold not met.</p></div>"}
    {"<div class='section'><h3>Auto Exits (SL/TP)</h3><table><tr><th>Ticker</th><th>Reason</th><th>P&L</th></tr>" + exits_html + "</table></div>" if auto_exits else ""}
    {"<div class='section'><h3>Open Positions</h3><table><tr><th>Ticker</th><th>Position</th><th>P&L</th><th>Thesis</th></tr>" + positions_html + "</table></div>" if positions_html else ""}
    {"<div class='section'><h3>Memory Applied</h3><p style='margin:0;font-size:13px;color:#2c3e50;background:#f8faff;border-left:3px solid #2a6db5;padding:10px 14px;border-radius:0 6px 6px 0;line-height:1.7'>" + memory + "</p></div>" if memory and memory != "—" else ""}
    {"<div class='section'><h3>Portfolio Notes</h3><p style='margin:0;font-size:13px;color:#2c3e50;line-height:1.7'>" + notes + "</p></div>" if notes and notes != "—" else ""}
  </div>
  <div class="footer">
    MeritQuant Autonomous Trader &nbsp;·&nbsp; Paper Portfolio &nbsp;·&nbsp; Not financial advice &nbsp;·&nbsp; github.com/Shrivathsav123/meritquant
  </div>
</div>
</body>
</html>
"""

    subject = f"MeritQuant Session Summary — {len(actions)} trades — {regime} — {now_str}"
    attachments = []
    if pdf_bytes:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        attachments.append((f"MeritQuant_{ts}.pdf", pdf_bytes))
    if xlsx_bytes:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        attachments.append((f"MeritQuant_{ts}.xlsx", xlsx_bytes))

    return _send(subject, html, attachments)


def send_sl_tp_alert(ticker: str, reason: str, pnl: float, portfolio: dict) -> bool:
    """Send immediate alert when stop loss or take profit triggers."""
    emoji   = "✅" if pnl > 0 else "🛑"
    colour  = "#1a6e3e" if pnl > 0 else "#9e2020"
    now_str = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    html = f"""
<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
  body{{font-family:Arial,sans-serif;background:#f4f6fa;margin:0;padding:20px}}
  .card{{background:#fff;border-radius:12px;max-width:500px;margin:0 auto;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.08)}}
  .header{{background:#0d1f3c;padding:20px 24px;color:#fff}}
  .header h1{{margin:0;font-size:18px;font-weight:800}}
  .bar{{background:{colour};padding:14px 24px;font-size:24px;font-weight:900;color:#fff}}
  .body{{padding:20px 24px}}
  .footer{{background:#f4f6fa;padding:12px 24px;font-size:11px;color:#999;text-align:center}}
</style>
</head>
<body>
<div class="card">
  <div class="header"><h1>MeritQuant — Auto Exit</h1></div>
  <div class="bar">{emoji} {ticker} &nbsp;|&nbsp; {pnl*100:+.1f}%</div>
  <div class="body">
    <p style="font-size:14px;color:#2c3e50;line-height:1.7"><strong>Reason:</strong> {reason}</p>
    <p style="font-size:13px;color:#666">Portfolio value after exit: <strong>${portfolio.get('total_value',0):,.0f}</strong><br>
    Cash available: <strong>${portfolio.get('cash',0):,.0f}</strong></p>
    <p style="font-size:11px;color:#999">{now_str}</p>
  </div>
  <div class="footer">MeritQuant Autonomous Trader &nbsp;·&nbsp; Not financial advice</div>
</div>
</body></html>
"""
    subject = f"MeritQuant {emoji} AUTO EXIT {ticker} — {pnl*100:+.1f}% — {reason}"
    return _send(subject, html)
