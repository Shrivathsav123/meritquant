"""Tests for news.py — article scoring, source extraction, and signal logic."""
import pytest
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from news import (
    clean_html,
    score_article,
    is_recent,
    _extract_source,
    get_news_for_scanner,
    HIGH_IMPACT,
    NEGATIVE_KEYWORDS,
)


# ── clean_html ────────────────────────────────────────────────────────────────

class TestCleanHtml:
    def test_strips_tags(self):
        assert clean_html("<b>Hello</b>") == "Hello"

    def test_strips_nested_tags(self):
        assert clean_html("<div><p>Text</p></div>") == "Text"

    def test_none_returns_empty_string(self):
        assert clean_html(None) == ""

    def test_plain_text_unchanged(self):
        assert clean_html("No tags here") == "No tags here"

    def test_strips_whitespace(self):
        assert clean_html("  <b>  word  </b>  ") == "word"


# ── score_article ─────────────────────────────────────────────────────────────

class TestScoreArticle:
    def test_positive_keyword_adds_score(self):
        score = score_article("earnings beat this quarter")
        assert score > 0

    def test_negative_keyword_subtracts_score(self):
        score = score_article("company announces layoffs and restructuring")
        assert score < 0

    def test_neutral_title_scores_zero(self):
        score = score_article("company releases quarterly report")
        assert score == 0

    def test_multiple_positive_keywords_stack(self):
        score = score_article("fda approved drug and acquisition by partner")
        single = score_article("fda approved")
        assert score >= single

    def test_conflicting_signals_cancel(self):
        # One positive + one negative
        score = score_article("earnings beat but guidance cut")
        assert score == 0

    def test_case_insensitive(self):
        score_lower = score_article("earnings beat")
        score_upper = score_article("EARNINGS BEAT")
        assert score_lower == score_upper

    def test_summary_also_scored(self):
        # keyword in summary only
        score = score_article("quarterly results", "earnings beat forecasts")
        assert score > 0

    def test_all_high_impact_keywords_score_positive(self):
        for kw in HIGH_IMPACT[:5]:
            assert score_article(kw) > 0

    def test_all_negative_keywords_score_negative(self):
        for kw in NEGATIVE_KEYWORDS[:5]:
            assert score_article(kw) < 0


# ── is_recent ─────────────────────────────────────────────────────────────────

class TestIsRecent:
    def _entry_with_time(self, hours_ago):
        t = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        return {"published_parsed": time.gmtime(t.timestamp())}

    def test_recent_article_returns_true(self):
        entry = self._entry_with_time(1)
        assert is_recent(entry, hours=24) is True

    def test_old_article_returns_false(self):
        entry = self._entry_with_time(48)
        assert is_recent(entry, hours=24) is False

    def test_exactly_at_boundary_is_not_recent(self):
        entry = self._entry_with_time(24.1)
        assert is_recent(entry, hours=24) is False

    def test_no_published_date_defaults_true(self):
        assert is_recent({}, hours=24) is True

    def test_custom_hours_window(self):
        entry = self._entry_with_time(6)
        assert is_recent(entry, hours=12) is True
        assert is_recent(entry, hours=4) is False


# ── _extract_source ───────────────────────────────────────────────────────────

class TestExtractSource:
    def test_cnbc_recognized(self):
        assert _extract_source("https://www.cnbc.com/article/123", {}) == "CNBC"

    def test_reuters_recognized(self):
        assert _extract_source("https://feeds.reuters.com/story", {}) == "Reuters"

    def test_yahoo_recognized(self):
        assert _extract_source("https://finance.yahoo.com/news/abc", {}) == "Yahoo Finance"

    def test_marketwatch_recognized(self):
        assert _extract_source("https://www.marketwatch.com/story", {}) == "MarketWatch"

    def test_unknown_source_returns_news(self):
        assert _extract_source("https://www.randomsite.com/article", {}) == "News"

    def test_case_insensitive_matching(self):
        assert _extract_source("https://WWW.CNBC.COM/article", {}) == "CNBC"


# ── get_news_for_scanner ──────────────────────────────────────────────────────

class TestGetNewsForScanner:
    def _make_article(self, score, recent=True):
        return {
            "title": "Test headline",
            "source": "CNBC",
            "score": score,
            "recent": recent,
            "bull": score > 0,
            "bear": score < 0,
        }

    def test_positive_articles_add_to_score(self):
        articles = [self._make_article(4), self._make_article(2)]
        with patch("news.get_stock_news", return_value=articles):
            signals, score_add = get_news_for_scanner("AAPL")
            assert score_add > 0

    def test_negative_articles_subtract_from_score(self):
        articles = [self._make_article(-4), self._make_article(-2)]
        with patch("news.get_stock_news", return_value=articles):
            signals, score_add = get_news_for_scanner("AAPL")
            assert score_add < 0

    def test_score_capped_at_3(self):
        articles = [self._make_article(10), self._make_article(10), self._make_article(10)]
        with patch("news.get_stock_news", return_value=articles):
            _, score_add = get_news_for_scanner("AAPL")
            assert score_add <= 3

    def test_score_floor_at_minus_value(self):
        articles = [self._make_article(-10), self._make_article(-10)]
        with patch("news.get_stock_news", return_value=articles):
            _, score_add = get_news_for_scanner("AAPL")
            assert score_add >= -4  # max -2 per article

    def test_zero_articles_returns_zero_score(self):
        with patch("news.get_stock_news", return_value=[]):
            signals, score_add = get_news_for_scanner("AAPL")
            assert score_add == 0
            assert signals == []

    def test_signals_list_contains_bull_articles(self):
        articles = [self._make_article(4)]
        with patch("news.get_stock_news", return_value=articles):
            signals, _ = get_news_for_scanner("AAPL")
            assert len(signals) == 1
            assert signals[0]["bull"] is True

    def test_signals_list_contains_bear_articles(self):
        articles = [self._make_article(-4)]
        with patch("news.get_stock_news", return_value=articles):
            signals, _ = get_news_for_scanner("AAPL")
            assert len(signals) == 1
            assert signals[0].get("bear") is True

    def test_neutral_articles_excluded_from_signals(self):
        articles = [self._make_article(0)]
        with patch("news.get_stock_news", return_value=articles):
            signals, score_add = get_news_for_scanner("AAPL")
            assert signals == []
            assert score_add == 0
