#!/usr/bin/env python3
"""news_fetcher.py — High-signal-only financial news engine for MeritQuant trader."""
import feedparser
import json
import os
import re
import time
from datetime import datetime, timezone

DATA_DIR     = "data"
NEWS_FILE    = f"{DATA_DIR}/news_feed.json"
SIGNALS_FILE = f"{DATA_DIR}/signals.json"
MAX_ARTICLES = 50
MIN_SCORE    = 2      # raised from 1 — only market-moving events survive

os.makedirs(DATA_DIR, exist_ok=True)

GLOBAL_FEEDS = [
    ("Reuters",     "https://feeds.reuters.com/reuters/businessNews"),
    ("CNBC",        "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Google News", "https://news.google.com/rss/search?q=stock+market+earnings+fed&hl=en-US&gl=US&ceid=US:en"),
]

# Only these categories survive — everything else is dropped
HIGH_IMPACT_TERMS = [
    "earnings", "revenue", "guidance", "eps", "profit",
    "federal reserve", "fomc", "interest rate", "rate cut", "rate hike",
    "inflation", "cpi", "pce", "core inflation",
    "jobs report", "nonfarm payroll", "unemployment", "gdp",
    "fda approved", "fda approval", "fda rejected", "fda rejection",
    "merger", "acquisition", "acquires", "acquired by", "takeover",
    "contract awarded", "billion contract", "government contract",
    "bankruptcy", "chapter 11", "defaults",
    "sanctions", "trade war", "tariff",
]

# HIGH = macro / systemic / mega-cap earnings — moves entire market
HIGH_IMPACT_TRIGGERS = [
    "federal reserve", "fomc", "interest rate", "rate cut", "rate hike",
    "inflation", "cpi", "pce", "core inflation",
    "jobs report", "nonfarm payroll", "unemployment rate", "gdp",
    "treasury", "yield curve",
    "sanctions", "trade war", "tariff",
    "bankruptcy", "chapter 11",
]

# MEDIUM = single-stock catalyst — moves one name
MEDIUM_IMPACT_TRIGGERS = [
    "earnings", "revenue", "guidance", "eps", "profit",
    "fda approved", "fda approval", "fda rejected", "fda rejection",
    "merger", "acquisition", "acquires", "acquired by", "takeover",
    "contract awarded", "billion contract", "government contract",
]

# Generic noise that gets dropped regardless of score
SPAM_PHRASES = [
    "sponsored", "advertisement",
    "best stocks to buy", "stocks to buy", "top picks",
    "you won't believe", "here's why you should",
    "should you buy", "is it a buy", "analyst says to buy",
    "5 stocks", "10 stocks", "these stocks",
    "what to watch", "week ahead", "morning briefing",
    "opinion:", "commentary:", "column:", "podcast:",
]

# Strict BULLISH signals — must clearly indicate positive event
BULL_SIGNALS = [
    "beats", "beat estimates", "beat expectations", "exceeds", "surpasses",
    "raises guidance", "raised guidance", "raises forecast",
    "approved", "fda approved", "clears", "granted",
    "awarded", "wins contract", "secures contract",
    "acquires", "merger agreed", "deal agreed",
    "record revenue", "record profit", "record earnings",
    "upgrades", "upgraded to buy",
    "buyback", "share repurchase", "dividend raised",
    "surges", "jumps", "soars", "rallies",
]

# Strict BEARISH signals — must clearly indicate negative event
BEAR_SIGNALS = [
    "misses", "miss estimates", "miss expectations", "falls short",
    "cuts guidance", "lowers guidance", "reduces forecast", "warns",
    "fda rejected", "fda rejection", "failed trial", "clinical hold",
    "investigation", "sec probe", "doj probe", "fraud",
    "bankruptcy", "chapter 11", "defaults", "insolvency",
    "layoffs", "job cuts", "mass layoffs",
    "downgraded", "downgrade to sell",
    "falls", "drops", "plunges", "tumbles", "slides",
    "recall", "product recall",
]

SOURCE_MAP = {
    "reuters.com":      "Reuters",
    "cnbc.com":         "CNBC",
    "marketwatch.com":  "MarketWatch",
    "wsj.com":          "WSJ",
    "bloomberg.com":    "Bloomberg",
    "ft.com":           "FT",
    "seekingalpha.com": "Seeking Alpha",
    "yahoo.com":        "Yahoo Finance",
    "investors.com":    "IBD",
}


def _clean(text):
    return re.sub(r'<[^>]+>', '', text or '').strip()


def _source_from_url(url):
    url_l = url.lower()
    for domain, name in SOURCE_MAP.items():
        if domain in url_l:
            return name
    return "News"


def _parse_published(entry):
    try:
        ts = entry.get("published_parsed") or entry.get("updated_parsed")
        if ts:
            return datetime.fromtimestamp(time.mktime(ts), tz=timezone.utc)
    except Exception:
        pass
    return datetime.now(timezone.utc)


def _is_breaking(pub_dt):
    return (datetime.now(timezone.utc) - pub_dt).total_seconds() < 7200


def _is_spam(title):
    title_l = title.lower()
    return any(phrase in title_l for phrase in SPAM_PHRASES)


def _quality_score(title, summary, company_name=""):
    text = (title + " " + summary).lower()
    score = 0
    for term in HIGH_IMPACT_TERMS:
        if term in text:
            score += 2
    if company_name and company_name.split()[0].lower() in text:
        score += 1
    return score


def _sentiment(title, summary):
    """Returns 'BULLISH' or 'BEARISH'. Returns None if unclear — caller drops the article."""
    text = (title + " " + summary).lower()
    bull = sum(1 for kw in BULL_SIGNALS if kw in text)
    bear = sum(1 for kw in BEAR_SIGNALS if kw in text)
    if bull > bear:
        return "BULLISH"
    if bear > bull:
        return "BEARISH"
    return None   # ambiguous — drop


def _market_impact(title, summary):
    """Returns 'HIGH', 'MEDIUM', or None (drop). None means below threshold."""
    text = (title + " " + summary).lower()
    for term in HIGH_IMPACT_TRIGGERS:
        if term in text:
            return "HIGH"
    for term in MEDIUM_IMPACT_TRIGGERS:
        if term in text:
            return "MEDIUM"
    return None   # no qualifying category — drop


def _find_tickers(text, ticker_list):
    found = []
    text_u = text.upper()
    for tk in ticker_list:
        if re.search(r'(?:^|\s|\$)' + re.escape(tk) + r'(?:\s|$|[^A-Z])', text_u):
            found.append(tk)
    return found[:5]


def _fetch_feed(url, max_entries=25):
    try:
        feed = feedparser.parse(url)
        return feed.entries[:max_entries]
    except Exception:
        return []


def _load_signal_tickers():
    try:
        signals = json.load(open(SIGNALS_FILE))
        return [(s["ticker"], s.get("name", s["ticker"])) for s in signals[:30]]
    except Exception:
        return []


def _make_article(entry, source_label, all_tickers, company_name=""):
    title   = _clean(entry.get("title", ""))
    summary = _clean(entry.get("summary", ""))
    link    = entry.get("link", "")
    if not title:
        return None
    if _is_spam(title):
        return None
    score = _quality_score(title, summary, company_name)
    if score < MIN_SCORE:
        return None
    sentiment = _sentiment(title, summary)
    if sentiment is None:
        return None   # can't classify clearly — drop
    impact = _market_impact(title, summary)
    if impact is None:
        return None   # no qualifying market-moving category — drop
    pub_dt = _parse_published(entry)
    return {
        "title":             title[:160],
        "source":            source_label or _source_from_url(link),
        "link":              link,
        "published_date":    pub_dt.isoformat(),
        "tickers_mentioned": _find_tickers(title + " " + summary,
                                           [tk for tk, _ in all_tickers]),
        "sentiment":         sentiment,
        "quality_score":     score,
        "market_impact":     impact,
        "is_breaking":       _is_breaking(pub_dt),
    }


def _impact_rank(impact):
    return {"HIGH": 2, "MEDIUM": 1}.get(impact, 0)


def run():
    print("[NewsFetcher] Starting high-signal pass...")
    all_tickers = _load_signal_tickers()
    articles    = []
    seen_titles = set()

    # Global market feeds
    for source_label, url in GLOBAL_FEEDS:
        entries = _fetch_feed(url, max_entries=25)
        for entry in entries:
            art = _make_article(entry, source_label, all_tickers)
            if art and art["title"] not in seen_titles:
                seen_titles.add(art["title"])
                articles.append(art)

    # Per-ticker Google News (top 2 per ticker from top 30 signals)
    for ticker, company in all_tickers:
        url     = f"https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en"
        entries = _fetch_feed(url, max_entries=5)
        added   = 0
        for entry in entries:
            if added >= 2:
                break
            art = _make_article(entry, "Google News", all_tickers, company)
            if art and art["title"] not in seen_titles:
                seen_titles.add(art["title"])
                articles.append(art)
                added += 1

    # Sort: breaking first, then HIGH before MEDIUM, then most recent
    articles.sort(
        key=lambda a: (
            a["is_breaking"],
            _impact_rank(a["market_impact"]),
            a["published_date"],
        ),
        reverse=True,
    )
    articles = articles[:MAX_ARTICLES]

    json.dump(articles, open(NEWS_FILE, "w"), indent=2)
    high  = sum(1 for a in articles if a["market_impact"] == "HIGH")
    med   = sum(1 for a in articles if a["market_impact"] == "MEDIUM")
    bull  = sum(1 for a in articles if a["sentiment"] == "BULLISH")
    bear  = sum(1 for a in articles if a["sentiment"] == "BEARISH")
    print(f"[NewsFetcher] {len(articles)} articles saved — "
          f"HIGH:{high} MED:{med} | BULL:{bull} BEAR:{bear}")
    return articles


if __name__ == "__main__":
    run()
