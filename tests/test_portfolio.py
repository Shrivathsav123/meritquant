"""Tests for trading/portfolio.py — position management and P&L logic."""
import pytest
import json
import os
import tempfile
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading.portfolio import (
    execute_buy,
    execute_sell,
    check_stop_losses,
    update_position_prices,
    load_portfolio,
    STARTING_BALANCE,
    MAX_POSITION_PCT,
    STOP_LOSS_PCT,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def fresh_portfolio():
    return {
        "balance":      STARTING_BALANCE,
        "cash":         STARTING_BALANCE,
        "positions":    {},
        "total_value":  STARTING_BALANCE,
        "pnl":          0.0,
        "pnl_pct":      0.0,
        "trades_count": 0,
        "wins":         0,
        "losses":       0,
    }


def portfolio_with_position(ticker="AAPL", entry_price=150.0, shares=10, trade_type="TECHNICAL"):
    p = fresh_portfolio()
    cost = round(entry_price * shares, 2)
    stop = round(entry_price * (1 + STOP_LOSS_PCT), 2)
    p["cash"] -= cost
    p["positions"][ticker] = {
        "ticker":        ticker,
        "name":          ticker,
        "shares":        shares,
        "entry_price":   entry_price,
        "current_price": entry_price,
        "cost":          cost,
        "value":         cost,
        "pnl":           0.0,
        "pnl_pct":       0.0,
        "stop_loss":     stop,
        "trade_type":    trade_type,
        "score":         6,
        "reasoning":     "test",
        "entry_date":    "2024-01-01T00:00:00",
    }
    return p


# ── execute_buy ────────────────────────────────────────────────────────────────

class TestExecuteBuy:
    def setup_method(self):
        self.p = fresh_portfolio()

    def _buy(self, ticker="AAPL", price=100.0, shares=10, **kw):
        defaults = dict(name="Apple", trade_type="TECHNICAL", reasoning="test", score=6)
        defaults.update(kw)
        with patch("trading.portfolio.load_trades", return_value=[]), \
             patch("trading.portfolio.save_trades"), \
             patch("trading.portfolio.save_portfolio"):
            return execute_buy(self.p, ticker, defaults["name"], price, shares,
                               defaults["trade_type"], defaults["reasoning"], defaults["score"])

    def test_successful_buy_returns_true(self):
        ok, msg = self._buy(price=100.0, shares=10)
        assert ok is True
        assert "Bought" in msg

    def test_cash_deducted_correctly(self):
        self._buy(price=100.0, shares=10)
        assert self.p["cash"] == pytest.approx(STARTING_BALANCE - 1000.0, abs=0.01)

    def test_position_created(self):
        self._buy(ticker="AAPL", price=100.0, shares=10)
        assert "AAPL" in self.p["positions"]

    def test_stop_loss_set_at_minus_7_pct(self):
        self._buy(ticker="AAPL", price=100.0, shares=10)
        pos = self.p["positions"]["AAPL"]
        assert pos["stop_loss"] == pytest.approx(100.0 * (1 + STOP_LOSS_PCT), abs=0.01)

    def test_trades_count_incremented(self):
        self._buy()
        assert self.p["trades_count"] == 1

    def test_insufficient_cash_rejected(self):
        self.p["cash"] = 500.0
        ok, msg = self._buy(price=100.0, shares=100)
        assert ok is False
        assert "Insufficient" in msg

    def test_position_size_capped_at_10_pct(self):
        # Try to buy 20% of portfolio in one go
        oversized_cost = self.p["total_value"] * 0.20
        shares = int(oversized_cost / 100.0)
        ok, msg = self._buy(price=100.0, shares=shares)
        if ok:
            pos = self.p["positions"]["AAPL"]
            max_allowed = self.p["total_value"] * MAX_POSITION_PCT
            assert pos["cost"] <= max_allowed + 100  # within one share tolerance

    def test_zero_shares_after_cap_rejected(self):
        # Very high price means 10% position < 1 share
        self.p["total_value"] = 100.0
        self.p["cash"] = 100.0
        ok, msg = self._buy(price=1_000_000.0, shares=1)
        assert ok is False

    def test_entry_price_stored(self):
        self._buy(ticker="TSLA", price=250.0, shares=5)
        assert self.p["positions"]["TSLA"]["entry_price"] == 250.0

    def test_cost_stored_correctly(self):
        self._buy(ticker="MSFT", price=300.0, shares=5)
        assert self.p["positions"]["MSFT"]["cost"] == pytest.approx(1500.0, abs=0.01)


# ── execute_sell ──────────────────────────────────────────────────────────────

class TestExecuteSell:
    def _sell(self, portfolio, ticker, price, reason="manual"):
        with patch("trading.portfolio.load_trades", return_value=[]), \
             patch("trading.portfolio.save_trades"), \
             patch("trading.portfolio.save_portfolio"):
            return execute_sell(portfolio, ticker, price, reason)

    def test_successful_sell_returns_true(self):
        p = portfolio_with_position("AAPL", entry_price=150.0, shares=10)
        ok, msg = self._sell(p, "AAPL", 160.0)
        assert ok is True

    def test_position_removed_after_sell(self):
        p = portfolio_with_position("AAPL", entry_price=150.0, shares=10)
        self._sell(p, "AAPL", 160.0)
        assert "AAPL" not in p["positions"]

    def test_cash_returned_after_sell(self):
        p = portfolio_with_position("AAPL", entry_price=150.0, shares=10)
        initial_cash = p["cash"]
        self._sell(p, "AAPL", 160.0)
        assert p["cash"] == pytest.approx(initial_cash + 160.0 * 10, abs=0.01)

    def test_win_counted_on_profit(self):
        p = portfolio_with_position("AAPL", entry_price=100.0, shares=10)
        self._sell(p, "AAPL", 120.0)
        assert p["wins"] == 1
        assert p["losses"] == 0

    def test_loss_counted_on_loss(self):
        p = portfolio_with_position("AAPL", entry_price=100.0, shares=10)
        self._sell(p, "AAPL", 90.0)
        assert p["losses"] == 1
        assert p["wins"] == 0

    def test_sell_nonexistent_ticker_returns_false(self):
        p = fresh_portfolio()
        ok, msg = self._sell(p, "FAKE", 100.0)
        assert ok is False
        assert "FAKE" in msg

    def test_pnl_message_contains_ticker(self):
        p = portfolio_with_position("NVDA", entry_price=200.0, shares=5)
        ok, msg = self._sell(p, "NVDA", 250.0)
        assert "NVDA" in msg

    def test_pnl_message_shows_profit(self):
        p = portfolio_with_position("NVDA", entry_price=200.0, shares=5)
        _, msg = self._sell(p, "NVDA", 250.0)
        assert "+" in msg  # profit shown with +

    def test_trades_count_incremented(self):
        p = portfolio_with_position("AAPL", entry_price=150.0, shares=10)
        initial = p["trades_count"]
        self._sell(p, "AAPL", 160.0)
        assert p["trades_count"] == initial + 1


# ── check_stop_losses ─────────────────────────────────────────────────────────

class TestCheckStopLosses:
    def _check(self, portfolio, prices):
        with patch("trading.portfolio.load_trades", return_value=[]), \
             patch("trading.portfolio.save_trades"), \
             patch("trading.portfolio.save_portfolio"):
            return check_stop_losses(portfolio, prices)

    def test_stop_triggered_below_stop_price(self):
        p = portfolio_with_position("AAPL", entry_price=100.0, shares=10)
        stop = p["positions"]["AAPL"]["stop_loss"]  # 93.0
        triggered = self._check(p, {"AAPL": stop - 1})
        assert len(triggered) == 1
        assert "AAPL" in triggered[0]

    def test_stop_not_triggered_above_stop_price(self):
        p = portfolio_with_position("AAPL", entry_price=100.0, shares=10)
        triggered = self._check(p, {"AAPL": 105.0})
        assert len(triggered) == 0

    def test_stop_triggered_at_exact_stop_price(self):
        p = portfolio_with_position("AAPL", entry_price=100.0, shares=10)
        stop = p["positions"]["AAPL"]["stop_loss"]
        triggered = self._check(p, {"AAPL": stop})
        assert len(triggered) == 1

    def test_no_prices_no_triggers(self):
        p = portfolio_with_position("AAPL", entry_price=100.0, shares=10)
        triggered = self._check(p, {})
        assert len(triggered) == 0

    def test_position_removed_after_stop(self):
        p = portfolio_with_position("AAPL", entry_price=100.0, shares=10)
        stop = p["positions"]["AAPL"]["stop_loss"]
        self._check(p, {"AAPL": stop - 5})
        assert "AAPL" not in p["positions"]

    def test_multiple_stops_all_trigger(self):
        p = portfolio_with_position("AAPL", entry_price=100.0, shares=5)
        with patch("trading.portfolio.load_trades", return_value=[]), \
             patch("trading.portfolio.save_trades"), \
             patch("trading.portfolio.save_portfolio"):
            execute_buy(p, "MSFT", "Microsoft", 200.0, 3, "TECHNICAL", "test", 5)
        stop_aapl = p["positions"]["AAPL"]["stop_loss"]
        stop_msft = p["positions"]["MSFT"]["stop_loss"]
        triggered = self._check(p, {"AAPL": stop_aapl - 1, "MSFT": stop_msft - 1})
        assert len(triggered) == 2

    def test_stop_loss_pct_is_minus_7(self):
        assert STOP_LOSS_PCT == pytest.approx(-0.07, abs=0.001)


# ── update_position_prices ─────────────────────────────────────────────────────

class TestUpdatePositionPrices:
    def test_pnl_updates_on_gain(self):
        p = portfolio_with_position("AAPL", entry_price=100.0, shares=10)
        update_position_prices(p, {"AAPL": 110.0})
        assert p["positions"]["AAPL"]["pnl"] == pytest.approx(100.0, abs=0.01)
        assert p["positions"]["AAPL"]["pnl_pct"] == pytest.approx(10.0, abs=0.01)

    def test_pnl_updates_on_loss(self):
        p = portfolio_with_position("AAPL", entry_price=100.0, shares=10)
        update_position_prices(p, {"AAPL": 90.0})
        assert p["positions"]["AAPL"]["pnl"] == pytest.approx(-100.0, abs=0.01)
        assert p["positions"]["AAPL"]["pnl_pct"] == pytest.approx(-10.0, abs=0.01)

    def test_total_value_includes_cash_and_positions(self):
        p = portfolio_with_position("AAPL", entry_price=100.0, shares=10)
        update_position_prices(p, {"AAPL": 100.0})
        assert p["total_value"] == pytest.approx(STARTING_BALANCE, abs=1.0)

    def test_missing_price_keeps_old_value(self):
        p = portfolio_with_position("AAPL", entry_price=100.0, shares=10)
        old_value = p["positions"]["AAPL"]["value"]
        update_position_prices(p, {})  # no price provided
        assert p["positions"]["AAPL"]["value"] == old_value

    def test_portfolio_pnl_pct_calculated(self):
        p = portfolio_with_position("AAPL", entry_price=100.0, shares=10)
        update_position_prices(p, {"AAPL": 150.0})
        assert p["pnl_pct"] != 0.0

    def test_max_position_pct_constant(self):
        assert MAX_POSITION_PCT == pytest.approx(0.10, abs=0.001)
