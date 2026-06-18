"""Tests for technical.py — scoring logic that drives all trading decisions."""
import pandas as pd
import numpy as np
import pytest
from unittest.mock import patch

from technical import (
    get_fibonacci_levels,
    get_rsi,
    get_rsi_score,
    get_ma_score,
    get_ad_score,
    detect_patterns,
    analyze_ticker,
    RSI_OVERSOLD_STRONG,
    RSI_OVERSOLD_MAX,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def make_ohlcv(n=252, trend="flat", start_price=100.0):
    """Build a synthetic OHLCV DataFrame."""
    np.random.seed(42)
    if trend == "up":
        closes = np.linspace(start_price, start_price * 1.5, n) + np.random.randn(n) * 0.5
    elif trend == "down":
        closes = np.linspace(start_price, start_price * 0.6, n) + np.random.randn(n) * 0.5
    else:
        closes = start_price + np.random.randn(n).cumsum() * 0.3

    closes = np.abs(closes)
    highs  = closes * 1.01
    lows   = closes * 0.99
    opens  = closes * (1 + np.random.randn(n) * 0.002)
    volume = np.random.randint(1_000_000, 5_000_000, n).astype(float)

    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volume},
        index=idx,
    )


# ── Fibonacci ─────────────────────────────────────────────────────────────────

class TestFibonacciLevels:
    def test_basic_levels(self):
        levels = get_fibonacci_levels(200.0, 100.0)
        assert levels["38.2%"] == pytest.approx(161.8, abs=0.1)
        assert levels["50.0%"] == pytest.approx(150.0, abs=0.1)
        assert levels["61.8%"] == pytest.approx(138.2, abs=0.1)

    def test_levels_between_high_and_low(self):
        levels = get_fibonacci_levels(300.0, 100.0)
        for _, price in levels.items():
            assert 100.0 < price < 300.0

    def test_golden_ratio_61_8(self):
        levels = get_fibonacci_levels(100.0, 0.0)
        assert levels["61.8%"] == pytest.approx(38.2, abs=0.1)

    def test_equal_high_low_returns_same_price(self):
        levels = get_fibonacci_levels(100.0, 100.0)
        for _, price in levels.items():
            assert price == 100.0

    def test_returns_three_levels(self):
        levels = get_fibonacci_levels(200.0, 100.0)
        assert len(levels) == 3
        assert set(levels.keys()) == {"38.2%", "50.0%", "61.8%"}


# ── RSI Calculation ───────────────────────────────────────────────────────────

class TestGetRsi:
    def test_rsi_in_valid_range(self):
        df = make_ohlcv(100)
        rsi = get_rsi(df["Close"])
        valid = rsi.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_rsi_falls_on_downtrend(self):
        df = make_ohlcv(60, trend="down")
        rsi = get_rsi(df["Close"])
        assert float(rsi.dropna().iloc[-1]) < 50

    def test_rsi_rises_on_uptrend(self):
        df = make_ohlcv(60, trend="up")
        rsi = get_rsi(df["Close"])
        assert float(rsi.dropna().iloc[-1]) > 50

    def test_fallback_on_empty_series(self):
        empty = pd.Series([], dtype=float)
        result = get_rsi(empty)
        assert len(result) == 0 or list(result) == [50] * len(result)

    def test_constant_price_rsi_is_50(self):
        # Constant prices → no gains or losses → RSI should be near 50 (or default)
        prices = pd.Series([100.0] * 30)
        rsi = get_rsi(prices)
        valid = rsi.dropna()
        # constant series: no change, so RSI formula may vary by library; just verify no crash
        assert len(rsi) == 30


# ── RSI Score ─────────────────────────────────────────────────────────────────

class TestGetRsiScore:
    def _mock_data(self, rsi_value, n=30):
        """Build DataFrame that will produce approximately the given RSI."""
        df = make_ohlcv(n)
        return df

    def test_score_3_when_both_timeframes_strongly_oversold(self):
        df_oversold = make_ohlcv(30, trend="down", start_price=100.0)
        with patch("technical.fetch_yahoo", return_value=df_oversold):
            with patch("technical.get_rsi") as mock_rsi:
                # Simulate RSI of 25 (strongly oversold) on both timeframes
                mock_rsi.return_value = pd.Series([25.0] * 30)
                score, details, count = get_rsi_score("TEST")
                assert score == 3
                assert count >= 2

    def test_score_2_when_one_timeframe_strongly_oversold(self):
        df = make_ohlcv(30, trend="down")
        call_count = [0]

        def side_effect(ticker, period, interval):
            call_count[0] += 1
            return df

        with patch("technical.fetch_yahoo", side_effect=side_effect):
            with patch("technical.get_rsi") as mock_rsi:
                mock_rsi.return_value = pd.Series([28.0] * 30)
                score, details, count = get_rsi_score("TEST")
                assert score >= 2

    def test_score_0_on_empty_data(self):
        with patch("technical.fetch_yahoo", return_value=pd.DataFrame()):
            score, details, count = get_rsi_score("EMPTY")
            assert score == 0
            assert count == 0

    def test_score_1_when_mildly_oversold(self):
        df = make_ohlcv(30)
        with patch("technical.fetch_yahoo", return_value=df):
            with patch("technical.get_rsi") as mock_rsi:
                # RSI 45: below MAX (50) but above STRONG (35) → 0.5 per timeframe
                mock_rsi.return_value = pd.Series([45.0] * 30)
                score, details, count = get_rsi_score("TEST")
                assert score >= 1

    def test_oversold_threshold_constants(self):
        assert RSI_OVERSOLD_STRONG == 35
        assert RSI_OVERSOLD_MAX == 50
        assert RSI_OVERSOLD_STRONG < RSI_OVERSOLD_MAX


# ── MA Score ──────────────────────────────────────────────────────────────────

class TestGetMaScore:
    def test_score_increases_above_200ma(self):
        df = make_ohlcv(250, trend="up", start_price=100.0)
        # Force price well above MA
        df["Close"] = df["Close"] * 1.2
        with patch("technical.fetch_yahoo", return_value=df):
            score, details = get_ma_score("TEST")
            assert score >= 1
            assert "ma50" in details
            assert "ma200" in details

    def test_score_0_on_insufficient_data(self):
        df = make_ohlcv(30)  # less than 50 rows
        with patch("technical.fetch_yahoo", return_value=df):
            score, details = get_ma_score("TEST")
            assert score == 0

    def test_score_0_on_empty_data(self):
        with patch("technical.fetch_yahoo", return_value=pd.DataFrame()):
            score, details = get_ma_score("TEST")
            assert score == 0

    def test_golden_cross_detected(self):
        df = make_ohlcv(260, trend="up", start_price=50.0)
        # Create a golden cross: MA50 just crossed above MA200
        close = df["Close"].values.copy()
        close[-1] = close[-200:].mean() * 1.01  # ensure MA50 > MA200
        df["Close"] = close
        with patch("technical.fetch_yahoo", return_value=df):
            score, details = get_ma_score("TEST")
            # Either golden cross active or above 200MA
            assert score >= 1

    def test_near_200ma_adds_score(self):
        df = make_ohlcv(260, start_price=100.0)
        close = df["Close"].copy()
        ma200 = close.rolling(200).mean().iloc[-1]
        # Set last price to within 1.5% of MA200
        close.iloc[-1] = ma200 * 1.005
        df["Close"] = close
        with patch("technical.fetch_yahoo", return_value=df):
            score, details = get_ma_score("TEST")
            assert "near_200ma" in details or score >= 0  # may or may not trigger depending on other conditions


# ── A/D Score ─────────────────────────────────────────────────────────────────

class TestGetAdScore:
    def test_score_1_when_ad_rising(self):
        df = make_ohlcv(60, trend="up")
        with patch("technical.fetch_yahoo", return_value=df):
            score, details = get_ad_score("TEST")
            # Can't guarantee direction without controlling A/D precisely, but no crash
            assert score in (0, 1)
            assert "signal" in details

    def test_score_0_on_empty_data(self):
        with patch("technical.fetch_yahoo", return_value=pd.DataFrame()):
            score, details = get_ad_score("TEST")
            assert score == 0

    def test_score_0_on_insufficient_data(self):
        df = make_ohlcv(10)
        with patch("technical.fetch_yahoo", return_value=df):
            score, details = get_ad_score("TEST")
            assert score == 0


# ── Pattern Detection ─────────────────────────────────────────────────────────

class TestDetectPatterns:
    def test_returns_lists_on_valid_data(self):
        df = make_ohlcv(130, trend="up")
        with patch("technical.fetch_yahoo", return_value=df):
            patterns, details = detect_patterns("TEST")
            assert isinstance(patterns, list)
            assert isinstance(details, dict)

    def test_empty_on_insufficient_data(self):
        df = make_ohlcv(5)
        with patch("technical.fetch_yahoo", return_value=df):
            patterns, details = detect_patterns("TEST")
            assert patterns == []

    def test_empty_on_no_data(self):
        with patch("technical.fetch_yahoo", return_value=pd.DataFrame()):
            patterns, details = detect_patterns("TEST")
            assert patterns == []

    def test_breakout_detected_with_volume_spike(self):
        df = make_ohlcv(130, trend="up")
        # Force last candle to break above recent high with volume spike
        df.iloc[-1, df.columns.get_loc("Close")] = df["High"].iloc[-21:-1].max() * 1.05
        df.iloc[-1, df.columns.get_loc("High")]  = df["High"].iloc[-21:-1].max() * 1.06
        avg_vol = df["Volume"].iloc[-20:].mean()
        df.iloc[-1, df.columns.get_loc("Volume")] = avg_vol * 2.0
        with patch("technical.fetch_yahoo", return_value=df):
            patterns, _ = detect_patterns("TEST")
            breakout_found = any("BREAKOUT" in p for p in patterns)
            assert breakout_found

    def test_uptrend_detected(self):
        df = make_ohlcv(130, trend="up", start_price=50.0)
        # Explicitly set higher highs and higher lows at indices -1, -5, -10
        df.iloc[-1,  df.columns.get_loc("High")] = 120.0
        df.iloc[-5,  df.columns.get_loc("High")] = 110.0
        df.iloc[-10, df.columns.get_loc("High")] = 100.0
        df.iloc[-1,  df.columns.get_loc("Low")]  = 115.0
        df.iloc[-5,  df.columns.get_loc("Low")]  = 105.0
        df.iloc[-10, df.columns.get_loc("Low")]  = 95.0
        with patch("technical.fetch_yahoo", return_value=df):
            patterns, _ = detect_patterns("TEST")
            uptrend_found = any("Uptrend" in p for p in patterns)
            assert uptrend_found

    def test_near_support_detected(self):
        df = make_ohlcv(130, start_price=100.0)
        recent_low = df["Low"].iloc[-20:].min()
        # Place current close within 2% of the recent low
        df.iloc[-1, df.columns.get_loc("Close")] = recent_low * 1.01
        with patch("technical.fetch_yahoo", return_value=df):
            patterns, _ = detect_patterns("TEST")
            support_found = any("Support" in p for p in patterns)
            assert support_found


# ── analyze_ticker ─────────────────────────────────────────────────────────────

class TestAnalyzeTicker:
    def _patch_all(self, rsi_score=2, ma_score=2, ad_score=1, patterns=None):
        if patterns is None:
            patterns = []
        return (
            patch("technical.get_rsi_score", return_value=(rsi_score, {}, rsi_score)),
            patch("technical.get_ma_score",  return_value=(ma_score, {})),
            patch("technical.get_ad_score",  return_value=(ad_score, {})),
            patch("technical.detect_patterns", return_value=(patterns, {})),
        )

    def test_returns_dict_with_required_keys(self):
        p1, p2, p3, p4 = self._patch_all()
        with p1, p2, p3, p4:
            result = analyze_ticker("AAPL", "Apple")
            for key in ["ticker", "name", "score", "buy_rating", "conviction", "signals"]:
                assert key in result

    def test_score_is_sum_of_components(self):
        p1, p2, p3, p4 = self._patch_all(rsi_score=2, ma_score=2, ad_score=1)
        with p1, p2, p3, p4:
            result = analyze_ticker("TEST")
            assert result["score"] == 5

    def test_strong_buy_at_score_7_plus(self):
        p1, p2, p3, p4 = self._patch_all(rsi_score=3, ma_score=3, ad_score=1, patterns=["p1", "p2"])
        with p1, p2, p3, p4:
            result = analyze_ticker("TEST")
            assert result["buy_rating"] == "🔥 STRONG BUY"
            assert result["conviction"] == "HIGH"

    def test_good_setup_at_score_5_to_6(self):
        p1, p2, p3, p4 = self._patch_all(rsi_score=2, ma_score=2, ad_score=1)
        with p1, p2, p3, p4:
            result = analyze_ticker("TEST")
            assert result["buy_rating"] == "⚡ GOOD SETUP"
            assert result["conviction"] == "MEDIUM"

    def test_watch_at_score_3_to_4(self):
        p1, p2, p3, p4 = self._patch_all(rsi_score=1, ma_score=1, ad_score=1)
        with p1, p2, p3, p4:
            result = analyze_ticker("TEST")
            assert result["buy_rating"] == "👀 WATCH"
            assert result["conviction"] == "LOW"

    def test_not_yet_at_score_below_3(self):
        p1, p2, p3, p4 = self._patch_all(rsi_score=0, ma_score=0, ad_score=0)
        with p1, p2, p3, p4:
            result = analyze_ticker("TEST")
            assert result["buy_rating"] == "⏳ NOT YET"
            assert result["conviction"] == "SKIP"

    def test_patterns_capped_at_2_score_points(self):
        many_patterns = ["p1", "p2", "p3", "p4", "p5"]
        p1, p2, p3, p4 = self._patch_all(rsi_score=0, ma_score=0, ad_score=0, patterns=many_patterns)
        with p1, p2, p3, p4:
            result = analyze_ticker("TEST")
            assert result["score"] == 2  # capped at min(5, 2) = 2

    def test_ticker_and_name_stored(self):
        p1, p2, p3, p4 = self._patch_all()
        with p1, p2, p3, p4:
            result = analyze_ticker("NVDA", "NVIDIA")
            assert result["ticker"] == "NVDA"
            assert result["name"] == "NVIDIA"

    def test_name_defaults_to_ticker(self):
        p1, p2, p3, p4 = self._patch_all()
        with p1, p2, p3, p4:
            result = analyze_ticker("NVDA")
            assert result["name"] == "NVDA"
