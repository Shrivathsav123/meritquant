"""Tests for trading/memory.py — lesson recording and pattern extraction."""
import pytest
import json
import os
from unittest.mock import patch, mock_open

from trading.memory import (
    record_lesson,
    get_memory_context,
    _extract_lesson,
    _extract_win_pattern,
    _check_ban_setup,
    load_memory,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def empty_memory():
    return {
        "lessons":          [],
        "winning_patterns": [],
        "macro_mistakes":   [],
        "sector_notes":     {},
        "banned_setups":    [],
        "last_updated":     None,
    }


def patched_record(ticker, action, pnl_pct, reasoning="test", sell_reason="test",
                   macro_env="NEUTRAL", sector="Tech"):
    mem_store = [empty_memory()]

    def fake_load():
        return mem_store[0]

    def fake_save(m):
        mem_store[0] = m

    with patch("trading.memory.load_memory", side_effect=fake_load), \
         patch("trading.memory.save_memory", side_effect=fake_save):
        record_lesson(ticker, action, pnl_pct, reasoning, sell_reason, macro_env, sector)

    return mem_store[0]


# ── record_lesson — losing trades ─────────────────────────────────────────────

class TestRecordLessonLosses:
    def test_loss_adds_to_lessons(self):
        mem = patched_record("AAPL", "SELL", -10.0)
        assert len(mem["lessons"]) == 1
        assert mem["lessons"][0]["ticker"] == "AAPL"

    def test_small_loss_not_added(self):
        mem = patched_record("AAPL", "SELL", -2.0)
        assert len(mem["lessons"]) == 0

    def test_lesson_capped_at_50(self):
        mem_store = [empty_memory()]
        mem_store[0]["lessons"] = [{"ticker": "X", "pnl_pct": -5.0, "lesson": "old"}] * 50

        def fake_load():
            return mem_store[0]
        def fake_save(m):
            mem_store[0] = m

        with patch("trading.memory.load_memory", side_effect=fake_load), \
             patch("trading.memory.save_memory", side_effect=fake_save):
            record_lesson("NEW", "SELL", -8.0, "test", "test", "NEUTRAL", "Tech")

        assert len(mem_store[0]["lessons"]) == 50
        assert mem_store[0]["lessons"][0]["ticker"] == "NEW"

    def test_bearish_macro_adds_macro_mistake(self):
        mem = patched_record("TSLA", "SELL", -8.0, macro_env="BEARISH")
        assert len(mem["macro_mistakes"]) == 1
        assert "TSLA" in mem["macro_mistakes"][0]["mistake"]

    def test_volatile_macro_adds_macro_mistake(self):
        mem = patched_record("NVDA", "SELL", -6.0, macro_env="VOLATILE")
        assert len(mem["macro_mistakes"]) == 1

    def test_neutral_macro_no_macro_mistake(self):
        mem = patched_record("AAPL", "SELL", -8.0, macro_env="NEUTRAL")
        assert len(mem["macro_mistakes"]) == 0

    def test_loss_increments_sector_losses(self):
        mem = patched_record("AAPL", "SELL", -5.0, sector="Technology")
        assert mem["sector_notes"]["Technology"]["losses"] == 1

    def test_lesson_contains_ticker(self):
        mem = patched_record("AMD", "SELL", -9.0)
        assert mem["lessons"][0]["ticker"] == "AMD"


# ── record_lesson — winning trades ───────────────────────────────────────────

class TestRecordLessonWins:
    def test_win_adds_to_winning_patterns(self):
        mem = patched_record("NVDA", "SELL", 12.0)
        assert len(mem["winning_patterns"]) == 1
        assert mem["winning_patterns"][0]["ticker"] == "NVDA"

    def test_small_win_not_added(self):
        mem = patched_record("AAPL", "SELL", 3.0)
        assert len(mem["winning_patterns"]) == 0

    def test_winning_patterns_capped_at_30(self):
        mem_store = [empty_memory()]
        mem_store[0]["winning_patterns"] = [{"ticker": "X", "pnl_pct": 10.0, "pattern": "p"}] * 30

        def fake_load():
            return mem_store[0]
        def fake_save(m):
            mem_store[0] = m

        with patch("trading.memory.load_memory", side_effect=fake_load), \
             patch("trading.memory.save_memory", side_effect=fake_save):
            record_lesson("NEW", "SELL", 20.0, "test", "test", "NEUTRAL", "Tech")

        assert len(mem_store[0]["winning_patterns"]) == 30

    def test_win_increments_sector_wins(self):
        mem = patched_record("MSFT", "SELL", 8.0, sector="Cloud")
        assert mem["sector_notes"]["Cloud"]["wins"] == 1


# ── _extract_lesson ───────────────────────────────────────────────────────────

class TestExtractLesson:
    def test_jobs_report_lesson(self):
        lesson = _extract_lesson("AAPL", -5.0, "held through nfp report", "", "NEUTRAL")
        assert "jobs" in lesson.lower() or "nfp" in lesson.lower() or "employment" in lesson.lower()

    def test_fed_rate_lesson(self):
        lesson = _extract_lesson("TSLA", -6.0, "", "rate hike fears hit growth", "NEUTRAL")
        assert "fed" in lesson.lower() or "rate" in lesson.lower()

    def test_earnings_lesson(self):
        lesson = _extract_lesson("SNAP", -8.0, "earnings miss guidance", "", "NEUTRAL")
        assert "earnings" in lesson.lower() or "entry" in lesson.lower()

    def test_bearish_macro_lesson(self):
        lesson = _extract_lesson("AMD", -7.0, "", "", "BEARISH")
        assert "BEARISH" in lesson or "macro" in lesson.lower()

    def test_default_lesson_contains_ticker(self):
        lesson = _extract_lesson("XYZ", -5.0, "", "", "NEUTRAL")
        assert "XYZ" in lesson


# ── _extract_win_pattern ──────────────────────────────────────────────────────

class TestExtractWinPattern:
    def test_oversold_rsi_pattern(self):
        pattern = _extract_win_pattern("NVDA", 15.0, "oversold RSI entry", "RISK ON")
        assert "oversold" in pattern.lower() or "rsi" in pattern.lower()

    def test_golden_cross_pattern(self):
        pattern = _extract_win_pattern("AAPL", 12.0, "golden cross momentum", "BULLISH")
        assert "golden cross" in pattern.lower()

    def test_default_pattern_contains_ticker_and_pnl(self):
        pattern = _extract_win_pattern("AMD", 10.0, "breakout", "NEUTRAL")
        assert "AMD" in pattern
        assert "10.0" in pattern or "10" in pattern


# ── _check_ban_setup ──────────────────────────────────────────────────────────

class TestCheckBanSetup:
    def test_death_cross_gets_banned(self):
        mem = empty_memory()
        _check_ban_setup(mem, "AAPL", "entered on death cross signal", -8.0)
        assert any("death cross" in b for b in mem["banned_setups"])

    def test_descending_channel_gets_banned(self):
        mem = empty_memory()
        _check_ban_setup(mem, "TSLA", "descending channel entry failed", -5.0)
        assert any("descending channel" in b for b in mem["banned_setups"])

    def test_no_banned_setup_in_reasoning(self):
        mem = empty_memory()
        _check_ban_setup(mem, "NVDA", "oversold RSI with volume", -4.0)
        assert len(mem["banned_setups"]) == 0

    def test_duplicate_setup_not_added_twice(self):
        mem = empty_memory()
        mem["banned_setups"].append("death cross — repeatedly fails, avoid")
        _check_ban_setup(mem, "AMD", "death cross entry", -6.0)
        count = sum(1 for b in mem["banned_setups"] if "death cross" in b)
        assert count == 1


# ── get_memory_context ────────────────────────────────────────────────────────

class TestGetMemoryContext:
    def test_empty_memory_returns_placeholder(self):
        with patch("trading.memory.load_memory", return_value=empty_memory()):
            result = get_memory_context()
            assert "No trade memory" in result

    def test_lessons_appear_in_context(self):
        mem = empty_memory()
        mem["lessons"] = [{
            "date": "2024-01-01", "ticker": "AAPL", "pnl_pct": -5.0,
            "lesson": "Do not hold into earnings", "macro_env": "NEUTRAL",
            "sector": "Tech", "action": "SELL", "reasoning": "", "sell_reason": ""
        }]
        with patch("trading.memory.load_memory", return_value=mem):
            result = get_memory_context()
            assert "AAPL" in result
            assert "LESSONS" in result

    def test_winning_patterns_appear_in_context(self):
        mem = empty_memory()
        mem["winning_patterns"] = [{
            "date": "2024-01-01", "ticker": "NVDA", "pnl_pct": 15.0,
            "pattern": "Golden cross worked", "macro_env": "RISK ON",
            "sector": "Tech", "action": "SELL", "reasoning": "", "sell_reason": ""
        }]
        with patch("trading.memory.load_memory", return_value=mem):
            result = get_memory_context()
            assert "WHAT HAS WORKED" in result

    def test_banned_setups_appear_in_context(self):
        mem = empty_memory()
        mem["banned_setups"] = ["death cross — repeatedly fails, avoid"]
        with patch("trading.memory.load_memory", return_value=mem):
            result = get_memory_context()
            assert "BANNED SETUPS" in result
            assert "death cross" in result

    def test_sector_win_rate_in_context(self):
        mem = empty_memory()
        mem["sector_notes"] = {"Tech": {"wins": 3, "losses": 1, "notes": []}}
        with patch("trading.memory.load_memory", return_value=mem):
            result = get_memory_context()
            assert "Tech" in result
            assert "75%" in result or "3W" in result

    def test_returns_string(self):
        with patch("trading.memory.load_memory", return_value=empty_memory()):
            result = get_memory_context()
            assert isinstance(result, str)
