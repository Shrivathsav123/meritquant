#!/usr/bin/env python3
"""news_fetcher.py — Structured financial news feed for MeritQuant trader."""
import feedparser
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta

DATA_DIR     = "data"
NEWS_FILE    = f"{DATA_DIR}/news_feed.json"
SIGNALS_FILE = f"{DATA_DIR}/signals.json"
MAX_ARTICLES = 80

os.makedirs(DATA_DIR, exist_ok=True)

GLOBAL_FEEDS = [
    ("Reuters",     "https://feeds.reuters.com/reuters/businessNews"),
    ("CNBC",        "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Google News", "https://news.google.com/rss/search?q=stock+market+earnings+fed&hl=en-US&gl=US&ceid=US:en"),
]

SPAM_PHRASES = [
    "sponsored", "advertisement", "best stocks to buy",
    "you won't believe", "top picks",
]

HIGH_IMPACT_TERMS = [
    "earnings", "revenue", "fda", "contract", "merger",
    "fed", "inflation", "gdp",
]

BULL_KEYWORDS = [
    "beat", "beats", "surges", "jumps", "gains", "rises", "upgraded",
    "approval", "approved", "awarded", "record", "raises guidance",
    "buyback", "dividend", "acquisition", "partnership",
]
BEAR_KEYWORDS = [
    "miss", "misses", "falls", "drops", "declines", "downgraded",
    "rejected", "recall", "investigation", "fraud", "layoffs",
    "guidance cut", "lowers guidance",
]

SOURCE_MAP = {
    "reuters.com":     "Reuters",
    "cnbc.com":        "CNBC",
    "marketwatch.com": "MarketWatch",
    "wsj.com":         "WSJ",
    "bloomberg.com":   "Bloomberg",
    "ft.com":          "FT",
    "seekingalpha.com": "Seeking Alpha",
    "yahoo.com":       "Yahoo Finance",
    "investors.com":   "IBD",
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
    text = (title + " " + summary).lower()
    bull = sum(1 for kw in BULL_KEYWORDS if kw in text)
    bear = sum(1 for kw in BEAR_KEYWORDS if kw in text)
    if bull > bear:
        return "bull"
    if bear > bull:
        return "bear"
    return "neutral"


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
    if score < 1:
        return None
    pub_dt = _parse_published(entry)
    return {
        "title":             title[:160],
        "source":            source_label or _source_from_url(link),
        "link":              link,
        "published_date":    pub_dt.isoformat(),
        "tickers_mentioned": _find_tickers(title + " " + summary,
                                           [tk for tk, _ in all_tickers]),
        "sentiment":         _sentiment(title, summary),
        "quality_score":     score,
        "is_breaking":       _is_breaking(pub_dt),
    }


def run():
    print("[NewsFetcher] Starting...")
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

    articles.sort(
        key=lambda a: (a["quality_score"], a["published_date"]),
        reverse=True,
    )
    articles = articles[:MAX_ARTICLES]

    json.dump(articles, open(NEWS_FILE, "w"), indent=2)
    print(f"[NewsFetcher] Saved {len(articles)} articles to {NEWS_FILE}")
    return articles


if __name__ == "__main__":
    run()
