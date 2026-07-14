# trading/trade_reporter.py
# Generates institutional-grade PDF trade reports and sends to Telegram

import os
import json
import requests
from datetime import datetime
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── Colour palette ──────────────────────────────────────────────
DARK_NAVY   = colors.HexColor("#0d1f3c")
MID_BLUE    = colors.HexColor("#1a3a6e")
ACCENT_BLUE = colors.HexColor("#2a6db5")
LIGHT_BLUE  = colors.HexColor("#e8f0fa")
GREEN       = colors.HexColor("#1a6e3e")
GREEN_LIGHT = colors.HexColor("#e8f5ee")
RED         = colors.HexColor("#9e2020")
RED_LIGHT   = colors.HexColor("#faeaea")
GOLD        = colors.HexColor("#b8860b")
GOLD_LIGHT  = colors.HexColor("#fef9e7")
GREY_DARK   = colors.HexColor("#2c3e50")
GREY_MID    = colors.HexColor("#7f8c8d")
GREY_LIGHT  = colors.HexColor("#f4f6f9")
WHITE       = colors.white
BLACK       = colors.black

def _styles():
    base = getSampleStyleSheet()
    return {
        "cover_title": ParagraphStyle("ct", fontSize=28, fontName="Helvetica-Bold",
            textColor=WHITE, alignment=TA_LEFT, leading=34, spaceAfter=6),
        "cover_sub": ParagraphStyle("cs", fontSize=13, fontName="Helvetica",
            textColor=colors.HexColor("#a0c4e8"), alignment=TA_LEFT, leading=18),
        "cover_meta": ParagraphStyle("cm", fontSize=10, fontName="Helvetica",
            textColor=colors.HexColor("#7ab0d4"), alignment=TA_LEFT, leading=14),
        "section_label": ParagraphStyle("sl", fontSize=8, fontName="Helvetica-Bold",
            textColor=ACCENT_BLUE, spaceBefore=16, spaceAfter=4,
            letterSpacing=2, alignment=TA_LEFT),
        "section_title": ParagraphStyle("st", fontSize=18, fontName="Helvetica-Bold",
            textColor=DARK_NAVY, spaceAfter=12, leading=22),
        "body": ParagraphStyle("b", fontSize=10.5, fontName="Helvetica",
            textColor=GREY_DARK, leading=16, spaceAfter=8),
        "body_bold": ParagraphStyle("bb", fontSize=10.5, fontName="Helvetica-Bold",
            textColor=DARK_NAVY, leading=16, spaceAfter=4),
        "small": ParagraphStyle("sm", fontSize=9, fontName="Helvetica",
            textColor=GREY_MID, leading=13),
        "metric_label": ParagraphStyle("ml", fontSize=8, fontName="Helvetica-Bold",
            textColor=GREY_MID, alignment=TA_CENTER, letterSpacing=1),
        "metric_value": ParagraphStyle("mv", fontSize=20, fontName="Helvetica-Bold",
            textColor=DARK_NAVY, alignment=TA_CENTER, leading=24),
        "metric_sub": ParagraphStyle("ms", fontSize=9, fontName="Helvetica",
            textColor=GREY_MID, alignment=TA_CENTER),
        "quote": ParagraphStyle("q", fontSize=11, fontName="Helvetica-Oblique",
            textColor=DARK_NAVY, leading=18, leftIndent=20, rightIndent=20,
            spaceBefore=8, spaceAfter=8),
        "disclaimer": ParagraphStyle("d", fontSize=8, fontName="Helvetica",
            textColor=GREY_MID, leading=12, alignment=TA_CENTER),
    }

def _divider(color=ACCENT_BLUE, thickness=1):
    return HRFlowable(width="100%", thickness=thickness, color=color,
                      spaceBefore=4, spaceAfter=8)

def _metric_cell(label, value, color=DARK_NAVY, bg=LIGHT_BLUE):
    return Table([
        [Paragraph(label, ParagraphStyle("l", fontSize=8, fontName="Helvetica-Bold",
            textColor=GREY_MID, alignment=TA_CENTER, letterSpacing=1))],
        [Paragraph(value, ParagraphStyle("v", fontSize=18, fontName="Helvetica-Bold",
            textColor=color, alignment=TA_CENTER, leading=22))],
    ], colWidths=[42*mm], rowHeights=[8*mm, 12*mm],
    style=TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), bg),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ROUNDEDCORNERS", [4]),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("LEFTPADDING", (0,0), (-1,-1), 4),
        ("RIGHTPADDING", (0,0), (-1,-1), 4),
    ]))

def build_trade_report(action, ticker, price, shares, reasoning,
                       sector, macro_env, probability_score,
                       macro_alignment, catalyst, risk_note,
                       portfolio_value, portfolio_pnl_pct,
                       sell_reason=None, pnl=None, pnl_pct=None,
                       lesson=None, target_pct=None, stop_pct=None,
                       hold_duration=None):
    """
    Build a full institutional-grade PDF trade report.
    Returns bytes of the PDF.
    """
    buf    = BytesIO()
    S      = _styles()
    now    = datetime.utcnow()
    is_buy = action == "BUY"
    accent = GREEN if is_buy else (GREEN if (pnl or 0) >= 0 else RED)
    action_label = "POSITION INITIATED" if is_buy else "POSITION CLOSED"

    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=0, bottomMargin=16*mm,
    )

    story = []

    # ── COVER HEADER ───────────────────────────────────────────
    cover_data = [[
        Table([
            [Paragraph("MERITQUANT", ParagraphStyle("at", fontSize=9,
                fontName="Helvetica-Bold", textColor=colors.HexColor("#7ab0d4"),
                letterSpacing=3))],
            [Paragraph(f"TRADE REPORT  ·  {action_label}", S["cover_title"])],
            [Paragraph(
                f"Account Z31989293  ·  Fidelity Brokerage  ·  "
                f"{now.strftime('%d %B %Y  %H:%M UTC')}",
                S["cover_sub"]
            )],
        ], colWidths=[170*mm],
        style=TableStyle([("BACKGROUND",(0,0),(-1,-1),DARK_NAVY),
                          ("TOPPADDING",(0,0),(-1,-1),6),
                          ("BOTTOMPADDING",(0,0),(-1,-1),6)]))
    ]]
    cover = Table(cover_data, colWidths=[170*mm],
        style=TableStyle([
            ("BACKGROUND",(0,0),(-1,-1), DARK_NAVY),
            ("TOPPADDING",(0,0),(-1,-1), 20),
            ("BOTTOMPADDING",(0,0),(-1,-1), 20),
            ("LEFTPADDING",(0,0),(-1,-1), 0),
            ("RIGHTPADDING",(0,0),(-1,-1), 0),
        ]))
    story.append(cover)
    story.append(Spacer(1, 8*mm))

    # ── KEY METRICS ROW ────────────────────────────────────────
    cost  = round(price * shares, 2)
    pnl_display = (
        f"${abs(pnl):,.0f}" if pnl is not None else "—"
    )
    pnl_pct_display = (
        f"{'+' if (pnl_pct or 0) >= 0 else ''}{pnl_pct:.2f}%"
        if pnl_pct is not None else "—"
    )
    pnl_color = (GREEN if (pnl or 0) >= 0 else RED) if not is_buy else ACCENT_BLUE
    score_color = (GREEN if (probability_score or 0) >= 7
                   else GOLD if (probability_score or 0) >= 5 else RED)

    metrics = Table([
        [
            _metric_cell("ACTION",   action,          WHITE if is_buy else WHITE,
                         GREEN if is_buy else ACCENT_BLUE),
            _metric_cell("TICKER",   f"${ticker}",    DARK_NAVY, LIGHT_BLUE),
            _metric_cell("PRICE",    f"${price:.2f}", DARK_NAVY, LIGHT_BLUE),
            _metric_cell("SHARES",   str(shares),     DARK_NAVY, LIGHT_BLUE),
            _metric_cell("COST / P&L",
                         pnl_display if not is_buy else f"${cost:,.0f}",
                         pnl_color,
                         GREEN_LIGHT if (pnl or 0) >= 0 and not is_buy else
                         RED_LIGHT   if (pnl or 0) <  0 and not is_buy else LIGHT_BLUE),
        ]
    ], colWidths=[34*mm]*5,
    style=TableStyle([
        ("ALIGN",  (0,0),(-1,-1), "CENTER"),
        ("VALIGN", (0,0),(-1,-1), "MIDDLE"),
        ("LEFTPADDING",  (0,0),(-1,-1), 3),
        ("RIGHTPADDING", (0,0),(-1,-1), 3),
    ]))
    story.append(metrics)
    story.append(Spacer(1, 6*mm))

    # ── SECTION 1 — MACRO CONTEXT ──────────────────────────────
    story.append(Paragraph("01  MACRO ENVIRONMENT", S["section_label"]))
    story.append(Paragraph("Macro Context & Regime Assessment", S["section_title"]))
    story.append(_divider())

    macro_table = Table([
        [
            Table([
                [Paragraph("MACRO REGIME", S["metric_label"])],
                [Paragraph(macro_env, ParagraphStyle("mr", fontSize=14,
                    fontName="Helvetica-Bold", textColor=DARK_NAVY, alignment=TA_CENTER))],
            ], colWidths=[80*mm], style=TableStyle([
                ("BACKGROUND",(0,0),(-1,-1), LIGHT_BLUE),
                ("TOPPADDING",(0,0),(-1,-1),8),
                ("BOTTOMPADDING",(0,0),(-1,-1),8),
            ])),
            Table([
                [Paragraph("PROBABILITY SCORE", S["metric_label"])],
                [Paragraph(f"{probability_score}/10", ParagraphStyle("ps", fontSize=20,
                    fontName="Helvetica-Bold", textColor=score_color, alignment=TA_CENTER))],
            ], colWidths=[80*mm], style=TableStyle([
                ("BACKGROUND",(0,0),(-1,-1),
                 GREEN_LIGHT if (probability_score or 0) >= 7
                 else GOLD_LIGHT if (probability_score or 0) >= 5 else RED_LIGHT),
                ("TOPPADDING",(0,0),(-1,-1),8),
                ("BOTTOMPADDING",(0,0),(-1,-1),8),
            ])),
        ]
    ], colWidths=[80*mm, 80*mm], style=TableStyle([
        ("LEFTPADDING",(0,0),(-1,-1),4),
        ("RIGHTPADDING",(0,0),(-1,-1),4),
    ]))
    story.append(macro_table)
    story.append(Spacer(1, 4*mm))

    if macro_alignment:
        story.append(Paragraph("<b>Macro Alignment:</b>", S["body_bold"]))
        story.append(Paragraph(macro_alignment, S["body"]))

    # ── SECTION 2 — TRADE THESIS ───────────────────────────────
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph("02  INVESTMENT THESIS", S["section_label"]))
    story.append(Paragraph(
        f"{'Entry Rationale' if is_buy else 'Exit Rationale'} — {ticker}",
        S["section_title"]
    ))
    story.append(_divider())

    if catalyst:
        story.append(Paragraph("<b>Primary Catalyst:</b>", S["body_bold"]))
        story.append(Paragraph(catalyst, S["body"]))
        story.append(Spacer(1, 2*mm))

    story.append(Paragraph("<b>Full Reasoning:</b>", S["body_bold"]))
    # Box the reasoning
    reason_box = Table([[Paragraph(reasoning or "—", S["body"])]],
        colWidths=[160*mm],
        style=TableStyle([
            ("BACKGROUND",(0,0),(-1,-1), LIGHT_BLUE),
            ("LEFTPADDING",(0,0),(-1,-1),12),
            ("RIGHTPADDING",(0,0),(-1,-1),12),
            ("TOPPADDING",(0,0),(-1,-1),10),
            ("BOTTOMPADDING",(0,0),(-1,-1),10),
            ("LINEBEFORE",(0,0),(0,-1), 4, ACCENT_BLUE),
        ]))
    story.append(reason_box)

    # ── SECTION 3 — EXIT ANALYSIS (SELL ONLY) ─────────────────
    if not is_buy and (sell_reason or lesson):
        story.append(Spacer(1, 4*mm))
        story.append(Paragraph("03  POST-TRADE ANALYSIS", S["section_label"]))
        story.append(Paragraph("Exit Analysis & Lessons Learned", S["section_title"]))
        story.append(_divider())

        pnl_box_color = GREEN_LIGHT if (pnl or 0) >= 0 else RED_LIGHT
        pnl_border    = GREEN       if (pnl or 0) >= 0 else RED

        result_table = Table([[
            Table([
                [Paragraph("REALISED P&L", S["metric_label"])],
                [Paragraph(
                    f"{'+' if (pnl or 0) >= 0 else ''}${abs(pnl or 0):,.0f}",
                    ParagraphStyle("rp", fontSize=22, fontName="Helvetica-Bold",
                        textColor=pnl_border, alignment=TA_CENTER))],
            ], colWidths=[78*mm], style=TableStyle([
                ("BACKGROUND",(0,0),(-1,-1), pnl_box_color),
                ("TOPPADDING",(0,0),(-1,-1),8),
                ("BOTTOMPADDING",(0,0),(-1,-1),8),
            ])),
            Table([
                [Paragraph("RETURN %", S["metric_label"])],
                [Paragraph(pnl_pct_display, ParagraphStyle("rr", fontSize=22,
                    fontName="Helvetica-Bold", textColor=pnl_border, alignment=TA_CENTER))],
            ], colWidths=[78*mm], style=TableStyle([
                ("BACKGROUND",(0,0),(-1,-1), pnl_box_color),
                ("TOPPADDING",(0,0),(-1,-1),8),
                ("BOTTOMPADDING",(0,0),(-1,-1),8),
            ])),
        ]], colWidths=[78*mm, 78*mm], style=TableStyle([
            ("LEFTPADDING",(0,0),(-1,-1),4),
            ("RIGHTPADDING",(0,0),(-1,-1),4),
        ]))
        story.append(result_table)
        story.append(Spacer(1, 4*mm))

        if sell_reason:
            story.append(Paragraph("<b>Why This Trade Was Closed:</b>", S["body_bold"]))
            story.append(Paragraph(sell_reason, S["body"]))
            story.append(Spacer(1, 2*mm))

        if lesson:
            lesson_box = Table([[
                Paragraph(f"<b>Lesson Learned:</b>  {lesson}", ParagraphStyle(
                    "lb", fontSize=10.5, fontName="Helvetica", textColor=DARK_NAVY,
                    leading=16, leftIndent=0))
            ]], colWidths=[160*mm],
            style=TableStyle([
                ("BACKGROUND",(0,0),(-1,-1), GOLD_LIGHT),
                ("LEFTPADDING",(0,0),(-1,-1),12),
                ("RIGHTPADDING",(0,0),(-1,-1),12),
                ("TOPPADDING",(0,0),(-1,-1),10),
                ("BOTTOMPADDING",(0,0),(-1,-1),10),
                ("LINEBEFORE",(0,0),(0,-1), 4, GOLD),
            ]))
            story.append(lesson_box)

    # ── SECTION 4 — RISK PARAMETERS ───────────────────────────
    story.append(Spacer(1, 4*mm))
    sec_num = "03" if is_buy else "04"
    story.append(Paragraph(f"{sec_num}  RISK PARAMETERS", S["section_label"]))
    story.append(Paragraph("Position Management Framework", S["section_title"]))
    story.append(_divider())

    risk_rows = [["Parameter", "Value", "Notes"]]
    if is_buy:
        risk_rows += [
            ["Entry Price",   f"${price:.2f}",          "Market execution"],
            ["Position Size", f"${cost:,.0f}",           f"{shares} shares"],
            ["Stop Loss",     f"-{stop_pct or 7:.1f}%",  f"${price*(1-(stop_pct or 7)/100):.2f} hard floor"],
            ["Target",        f"+{target_pct or 15:.1f}%", f"${price*(1+(target_pct or 15)/100):.2f} primary target"],
            ["Hold Duration", hold_duration or "2-3 weeks", "Subject to thesis review"],
            ["Sector",        sector or "—",              "Portfolio allocation context"],
        ]
    else:
        risk_rows += [
            ["Entry Price",   f"${price:.2f}",           "Original entry"],
            ["Exit Price",    f"${price:.2f}",            "Execution price"],
            ["Realised P&L",  pnl_display,                pnl_pct_display],
            ["Sector",        sector or "—",              ""],
            ["Macro at Exit", macro_env,                  "Regime at time of exit"],
        ]

    if risk_note:
        risk_rows.append(["Risk Note", risk_note[:60], ""])

    risk_table = Table(risk_rows,
        colWidths=[45*mm, 45*mm, 70*mm],
        style=TableStyle([
            ("BACKGROUND",   (0,0),(-1,0),  DARK_NAVY),
            ("TEXTCOLOR",    (0,0),(-1,0),  WHITE),
            ("FONTNAME",     (0,0),(-1,0),  "Helvetica-Bold"),
            ("FONTSIZE",     (0,0),(-1,0),  9),
            ("FONTNAME",     (0,1),(-1,-1), "Helvetica"),
            ("FONTSIZE",     (0,1),(-1,-1), 9),
            ("TEXTCOLOR",    (0,1),(-1,-1), GREY_DARK),
            ("ROWBACKGROUNDS",(0,1),(-1,-1), [WHITE, GREY_LIGHT]),
            ("GRID",         (0,0),(-1,-1), 0.5, colors.HexColor("#dde4ee")),
            ("TOPPADDING",   (0,0),(-1,-1), 7),
            ("BOTTOMPADDING",(0,0),(-1,-1), 7),
            ("LEFTPADDING",  (0,0),(-1,-1), 10),
        ]))
    story.append(risk_table)

    # ── PORTFOLIO CONTEXT ──────────────────────────────────────
    story.append(Spacer(1, 4*mm))
    port_row = Table([[
        Paragraph(
            f"<b>Portfolio Value:</b>  ${portfolio_value:,.0f}  "
            f"&nbsp;&nbsp;|&nbsp;&nbsp;  "
            f"<b>Total P&L:</b>  "
            f"{'+' if portfolio_pnl_pct >= 0 else ''}{portfolio_pnl_pct:.2f}%  "
            f"from $183,000 starting capital",
            ParagraphStyle("pc", fontSize=9.5, fontName="Helvetica",
                textColor=GREY_DARK, alignment=TA_CENTER)
        )
    ]], colWidths=[160*mm],
    style=TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), GREY_LIGHT),
        ("TOPPADDING",(0,0),(-1,-1),8),
        ("BOTTOMPADDING",(0,0),(-1,-1),8),
    ]))
    story.append(port_row)

    # ── DISCLAIMER ─────────────────────────────────────────────
    story.append(Spacer(1, 6*mm))
    story.append(_divider(GREY_MID, 0.5))
    story.append(Paragraph(
        "This report is generated by the MeritQuant autonomous trading system for internal "
        "review purposes only. All positions are paper trades. This document does not "
        "constitute investment advice. Not financial advice.",
        S["disclaimer"]
    ))

    doc.build(story)
    buf.seek(0)
    return buf.read()

def send_trade_report(action, ticker, price, shares, reasoning,
                      sector, macro_env, probability_score,
                      macro_alignment, catalyst, risk_note,
                      portfolio_value, portfolio_pnl_pct,
                      sell_reason=None, pnl=None, pnl_pct=None,
                      lesson=None, target_pct=None, stop_pct=None,
                      hold_duration=None):
    """Build PDF and send to Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Reporter] No Telegram credentials — skipping report")
        return

    try:
        pdf_bytes = build_trade_report(
            action=action, ticker=ticker, price=price, shares=shares,
            reasoning=reasoning, sector=sector, macro_env=macro_env,
            probability_score=probability_score, macro_alignment=macro_alignment,
            catalyst=catalyst, risk_note=risk_note,
            portfolio_value=portfolio_value, portfolio_pnl_pct=portfolio_pnl_pct,
            sell_reason=sell_reason, pnl=pnl, pnl_pct=pnl_pct,
            lesson=lesson, target_pct=target_pct, stop_pct=stop_pct,
            hold_duration=hold_duration,
        )

        now      = datetime.utcnow().strftime("%d%b%Y_%H%M")
        filename = f"MERITQUANT_{action}_{ticker}_{now}.pdf"

        caption = (
            f"MERITQUANT TRADE REPORT\n"
            f"{action} ${ticker} @ ${price:.2f}\n"
            f"Probability Score: {probability_score}/10\n"
            f"Sector: {sector or 'N/A'}\n"
            f"Macro: {macro_env}"
        )
        if not is_buy := (action == "BUY"):
            pnl_sign = "+" if (pnl or 0) >= 0 else ""
            caption += f"\nP&L: {pnl_sign}${abs(pnl or 0):,.0f} ({pnl_sign}{pnl_pct or 0:.2f}%)"

        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument",
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
            files={"document": (filename, pdf_bytes, "application/pdf")},
            timeout=30,
        )
        print(f"[Reporter] Sent PDF report for {action} {ticker}")

    except Exception as e:
        print(f"[Reporter] Error building/sending report: {e}")
