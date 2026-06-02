# news.py — Ticker-specific news scanner
import feedparser
import requests
from datetime import datetime, timezone, timedelta
from universe import ALL_US, NSE_STOCKS, STOCK_TO_ETF

HIGH_IMPACT_KEYWORDS = [
    "earnings beat", "beats estimates", "guidance raised", "record profit",
    "trump", "executive order", "government contract", "pentagon contract",
    "billion dollar", "fda approval", "drug approved", "acquisition",
    "merger", "buyback", "dividend increase", "upgrade", "buy rating",
    "target raised", "outperform", "rate cut", "stimulus",
    "fii buying", "bulk deal", "promoter buying",
]

NEGATIVE_KEYWORDS = [
    "earnings miss", "guidance lowered", "profit warning",
    "fraud", "investigation", "downgrade", "sell rating",
    "target cut", "underperform", "contract cancelled",
    "doge cut", "fine", "penalty", "lawsuit", "recall",
]

def is_recent(entry, hours=24):
    try:
        dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        return dt > datetime.now(timezone.utc) - timedelta(hours=hours)
    except:
        return True

def scan_news_for_ticker(ticker, company_name=""):
    """
    Search Google News specifically for this ticker and company.
    Only returns news that directly mentions the stock.
    """
    news_signals = []
    news_score   = 0

    # Clean up ticker for search
    clean_ticker  = ticker.replace(".NS", "").replace(".BO", "").replace("^", "")
    clean_company = (company_name or clean_ticker).split(" ")[0]  # First word of company name

    # Build specific search queries for this ticker
    if ".NS" in ticker or ".BO" in ticker:
        # Indian stock — search with NSE/BSE context
        queries = [
            f"{clean_company} NSE stock results earnings",
            f"{clean_ticker} India stock news",
        ]
    else:
        # US stock — search with ticker symbol
        queries = [
            f"{clean_ticker} stock news earnings",
            f"{clean_company} stock analyst",
        ]

    for query in queries:
        try:
            url  = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
            feed = feedparser.parse(url)

            for entry in feed.entries[:5]:
                if not is_recent(entry):
                    continue

                title   = entry.get("title", "")
                summary = entry.get("summary", "")
                full    = f"{title} {summary}".lower()

                # STRICT CHECK — ticker or company must be in the article
                ticker_mentioned  = clean_ticker.lower() in full
                company_mentioned = clean_company.lower() in full

                if not ticker_mentioned and not company_mentioned:
                    continue  # Skip — not about this stock

                # Score the news
                article_score = 0
                matched_pos   = []
                matched_neg   = []

                for kw in HIGH_IMPACT_KEYWORDS:
                    if kw in full:
                        article_score += 2 if kw in ["earnings beat", "trump", "government contract", "fda approval"] else 1
                        matched_pos.append(kw)

                for kw in NEGATIVE_KEYWORDS:
                    if kw in full:
                        article_score -= 2
                        matched_neg.append(kw)

                news_signals.append({
                    "title":    title[:120],
                    "score":    article_score,
                    "positive": matched_pos[:3],
                    "negative": matched_neg[:3],
                    "url":      entry.get("link", ""),
                    "time":     entry.get("published", ""),
                })
                news_score += max(article_score, 0)

        except Exception as e:
            pass

    return min(news_score, 3), news_signals[:3]


def get_related_etfs(ticker):
    """Get ETFs that should also be flagged when ticker has a catalyst."""
    return STOCK_TO_ETF.get(ticker, [])
