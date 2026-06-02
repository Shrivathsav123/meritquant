# news.py — Scans news and adds to stock score
import feedparser
import requests
from datetime import datetime, timezone, timedelta
from universe import ALL_US, NSE_STOCKS, STOCK_TO_ETF

NEWS_SOURCES = [
    "https://news.google.com/rss/search?q=stock+earnings+contract+government+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=Trump+company+stock+deal+tariff+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=earnings+beat+guidance+raised+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=government+contract+awarded+billion+2026&hl=en-US&gl=US&ceid=US:en",
    "https://feeds.reuters.com/reuters/businessNews",
]

NSE_NEWS_SOURCES = [
    "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "https://news.google.com/rss/search?q=NSE+India+earnings+results+contract+2026&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=FII+buying+India+bulk+deal+2026&hl=en-IN&gl=IN&ceid=IN:en",
]

HIGH_IMPACT_KEYWORDS = [
    "earnings beat", "beats estimates", "guidance raised", "record profit",
    "trump", "executive order", "government contract", "pentagon contract",
    "billion dollar contract", "fda approval", "drug approved",
    "acquisition", "merger", "buyback", "dividend increase",
    "upgrade", "buy rating", "target raised", "outperform",
    "fii buying", "bulk deal", "promoter buying",
    "rate cut", "stimulus", "infrastructure bill",
]

NEGATIVE_KEYWORDS = [
    "earnings miss", "guidance lowered", "profit warning",
    "sebi notice", "fraud", "investigation", "downgrade",
    "sell rating", "target cut", "underperform",
    "contract cancelled", "doge cut", "budget cut",
    "fine", "penalty", "lawsuit",
]

def is_recent(entry, hours=24):
    try:
        dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        return dt > datetime.now(timezone.utc) - timedelta(hours=hours)
    except:
        return True

def scan_news_for_ticker(ticker, company_name=""):
    """Scan all news sources for mentions of a specific ticker."""
    news_signals = []
    news_score = 0
    company_lower = (company_name or ticker).lower()
    ticker_lower = ticker.lower().replace(".ns", "").replace(".bo", "")

    all_sources = NEWS_SOURCES
    if ".NS" in ticker or ".BO" in ticker:
        all_sources = NSE_NEWS_SOURCES

    for url in all_sources:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:20]:
                if not is_recent(entry): continue
                title = entry.get("title", "").lower()
                summary = entry.get("summary", "").lower()
                full = f"{title} {summary}"

                # Check if this news is about our ticker
                if ticker_lower not in full and company_lower[:8] not in full:
                    continue

                # Score the news
                article_score = 0
                matched_positive = []
                matched_negative = []

                for kw in HIGH_IMPACT_KEYWORDS:
                    if kw in full:
                        article_score += 2 if kw in ["earnings beat", "trump", "government contract", "fda approval"] else 1
                        matched_positive.append(kw)

                for kw in NEGATIVE_KEYWORDS:
                    if kw in full:
                        article_score -= 2
                        matched_negative.append(kw)

                if article_score != 0 or ticker_lower in full:
                    news_signals.append({
                        "title":    entry.get("title", "")[:120],
                        "score":    article_score,
                        "positive": matched_positive[:3],
                        "negative": matched_negative[:3],
                        "url":      entry.get("link", ""),
                        "time":     entry.get("published", ""),
                    })
                    news_score += max(article_score, 0)

        except Exception as e:
            pass

    # Cap news score at 3
    return min(news_score, 3), news_signals[:3]


def get_related_etfs(ticker):
    """Get ETFs that should also be flagged when ticker has a catalyst."""
    return STOCK_TO_ETF.get(ticker, [])
