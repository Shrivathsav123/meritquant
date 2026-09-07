"""
Unit tests for the Bond-Dollar-Metals-Crypto Cascade Engine (BDMC).

Validates two historical rate-cut-cycle episodes:
  1. Fed pivot cycle (2018-12 → 2019-07): 10Y falling, real yield dropping,
     DXY falling → gold LONG, GDX LONG, KRE steepening
  2. COVID emergency cut cycle (2020-03): 10Y collapsing, real yield cratering,
     DXY initially spiking then falling → gold LONG, silver LONG, BTC emerging

Tests use fully mocked I/O — no network calls.
"""

from __future__ import annotations

import types
import pytest
import pandas as pd
import numpy as np

from unittest.mock import patch, MagicMock

from trading.bdmc_engine import (
    BDMCScanResult,
    BDMCSignal,
    MacroFlags,
    RSISignal,
    CONFIDENCE_THRESHOLD,
    classify_copper_regime,
    classify_holding_period,
    compute_rsi_signal,
    score_confidence,
    build_signal,
    sector_is_extended,
    rsi_percentile_6m,
    format_bdmc_report_section,
    format_bdmc_telegram,
    fetch_real_yield,
    fetch_2s10s_trend,
    fetch_dxy_trend,
    gdx_equity_beta_penalty,
    run_bdmc_scan,
    UNIVERSE,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_price_series(values: list[float]) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from a list of close prices."""
    return pd.DataFrame(
        {"close": values, "open": values, "high": values, "low": values, "volume": [1e6] * len(values)},
        index=pd.date_range("2024-01-01", periods=len(values), freq="1D"),
    )


def _make_rsi_signal(symbol: str, tf: str, rsi: float, series: str) -> RSISignal:
    meta = UNIVERSE.get(symbol, {})
    return RSISignal(
        symbol=symbol,
        timeframe=tf,
        rsi_value=rsi,
        direction_interpretation=meta.get("direction_interpretation", "PRICE_RISE_BULLISH"),
        series=series,
        overbought=(rsi >= 70),
        oversold=(rsi <= 30),
    )


# ---------------------------------------------------------------------------
# compute_rsi_signal
# ---------------------------------------------------------------------------

class TestComputeRsiSignal:
    def test_rising_rsi_detected(self):
        # Build price series that will generate a rising RSI (prices trending up)
        base = list(range(50, 90))
        df = _make_price_series(base)
        sig = compute_rsi_signal("GC=F", df, "1D")
        assert sig is not None
        assert sig.symbol == "GC=F"
        assert sig.timeframe == "1D"
        assert sig.series in ("rising", "flat", "falling")
        assert 0 <= sig.rsi_value <= 100

    def test_returns_none_on_insufficient_data(self):
        df = _make_price_series([100.0] * 5)  # too few bars
        sig = compute_rsi_signal("GC=F", df, "1H")
        assert sig is None

    def test_direction_interpretation_preserved(self):
        df = _make_price_series(list(range(40, 80)))
        sig = compute_rsi_signal("TNX", df, "4H")
        assert sig is not None
        assert sig.direction_interpretation == "YIELD_RISE_BEARISH_BONDS"

    def test_overbought_flag(self):
        # Force high RSI by using a strongly trending up series
        vals = [10 + i * 3 for i in range(30)]
        df = _make_price_series(vals)
        sig = compute_rsi_signal("GC=F", df, "1D")
        assert sig is not None
        if sig.rsi_value >= 70:
            assert sig.overbought is True

    def test_oversold_flag(self):
        # Force low RSI by using a strongly declining series
        vals = [100 - i * 3 for i in range(30)]
        df = _make_price_series(vals)
        sig = compute_rsi_signal("SI=F", df, "1D")
        assert sig is not None
        if sig.rsi_value <= 30:
            assert sig.oversold is True


# ---------------------------------------------------------------------------
# classify_copper_regime
# ---------------------------------------------------------------------------

class TestCopperRegime:
    def test_real_yield_safe_haven(self):
        gold_sigs = [_make_rsi_signal("GC=F", "1D", 58.0, "rising")]
        copper_sigs = [_make_rsi_signal("HG=F", "1D", 38.0, "falling")]
        regime = classify_copper_regime(gold_sigs, copper_sigs)
        assert regime == "REAL_YIELD_SAFE_HAVEN"

    def test_reflation(self):
        gold_sigs = [_make_rsi_signal("GC=F", "1D", 62.0, "rising")]
        copper_sigs = [_make_rsi_signal("HG=F", "1D", 65.0, "rising")]
        regime = classify_copper_regime(gold_sigs, copper_sigs)
        assert regime == "REFLATION"

    def test_risk_off(self):
        gold_sigs = [_make_rsi_signal("GC=F", "1D", 35.0, "falling")]
        copper_sigs = [_make_rsi_signal("HG=F", "1D", 30.0, "falling")]
        regime = classify_copper_regime(gold_sigs, copper_sigs)
        assert regime == "RISK_OFF"

    def test_empty_signals(self):
        regime = classify_copper_regime([], [])
        assert regime == "UNKNOWN"


# ---------------------------------------------------------------------------
# classify_holding_period
# ---------------------------------------------------------------------------

class TestHoldingPeriod:
    def test_scalp_short_timeframes_only(self):
        flags = MacroFlags()
        hp = classify_holding_period(["1H", "2H", "3H"], flags)
        assert hp == "Scalp"

    def test_swing_with_4h(self):
        flags = MacroFlags()
        hp = classify_holding_period(["4H"], flags)
        assert hp == "Swing"

    def test_swing_with_daily(self):
        flags = MacroFlags()
        hp = classify_holding_period(["1D"], flags)
        assert hp == "Swing"

    def test_position_daily_plus_structural(self):
        flags = MacroFlags(TREASURY_BUYBACK=True)
        hp = classify_holding_period(["1D", "4H"], flags)
        assert hp == "Position"

    def test_position_foreign_selling(self):
        flags = MacroFlags(FOREIGN_HOLDER_FLOW="SELLING")
        hp = classify_holding_period(["1D"], flags)
        assert hp == "Position"


# ---------------------------------------------------------------------------
# score_confidence
# ---------------------------------------------------------------------------

class TestScoreConfidence:
    def _gold_signals(self, series="rising"):
        return [
            _make_rsi_signal("GC=F", tf, 60.0, series)
            for tf in ("1H", "2H", "4H", "1D")
        ]

    def test_bullish_score_gold_multi_timeframe(self):
        sigs = self._gold_signals("rising")
        score, agreeing, rationale = score_confidence(
            rsi_signals=sigs,
            target_direction="bullish",
            real_yield=0.2,
            dxy_trend="FALLING",
            macro_flags=MacroFlags(),
            symbol="GC=F",
        )
        assert score >= CONFIDENCE_THRESHOLD
        assert "1D" in agreeing
        assert any("DXY falling" in r for r in rationale)

    def test_real_yield_headwind_reduces_score(self):
        sigs = [_make_rsi_signal("GC=F", "4H", 55.0, "rising")]
        score_low_ry, _, _ = score_confidence(
            rsi_signals=sigs,
            target_direction="bullish",
            real_yield=0.1,
            dxy_trend="FLAT",
            macro_flags=MacroFlags(),
            symbol="GC=F",
        )
        score_high_ry, _, _ = score_confidence(
            rsi_signals=sigs,
            target_direction="bullish",
            real_yield=2.0,
            dxy_trend="FLAT",
            macro_flags=MacroFlags(),
            symbol="GC=F",
        )
        assert score_low_ry > score_high_ry

    def test_kre_flattening_reduces_score(self):
        sigs = [_make_rsi_signal("KRE", "4H", 55.0, "rising"),
                _make_rsi_signal("KRE", "1D", 58.0, "rising")]
        score_flat, _, rationale = score_confidence(
            rsi_signals=sigs,
            target_direction="bullish",
            real_yield=None,
            dxy_trend="FLAT",
            macro_flags=MacroFlags(),
            symbol="KRE",
            spread_trend="FLATTENING",
        )
        score_steep, _, _ = score_confidence(
            rsi_signals=sigs,
            target_direction="bullish",
            real_yield=None,
            dxy_trend="FLAT",
            macro_flags=MacroFlags(),
            symbol="KRE",
            spread_trend="STEEPENING",
        )
        assert score_steep > score_flat
        assert any("FLATTENING" in r or "compress" in r for r in rationale)

    def test_buyback_boosts_gold(self):
        sigs = [_make_rsi_signal("GC=F", "1D", 62.0, "rising")]
        flags_no_buyback = MacroFlags(TREASURY_BUYBACK=False)
        flags_buyback = MacroFlags(TREASURY_BUYBACK=True,
                                   treasury_buyback_detail="TGA buyback active")
        s_no, _, _ = score_confidence(sigs, "bullish", 0.3, "FALLING", flags_no_buyback, "GC=F")
        s_yes, _, rat = score_confidence(sigs, "bullish", 0.3, "FALLING", flags_buyback, "GC=F")
        assert s_yes > s_no
        assert any("buyback" in r.lower() for r in rat)

    def test_gdx_penalty_applied(self):
        sigs = [_make_rsi_signal("GDX", "1D", 65.0, "rising"),
                _make_rsi_signal("GDX", "4H", 62.0, "rising")]
        s_no_pen, _, _ = score_confidence(sigs, "bullish", 0.3, "FALLING", MacroFlags(), "GDX", gdx_penalty=0)
        s_pen, _, _ = score_confidence(sigs, "bullish", 0.3, "FALLING", MacroFlags(), "GDX", gdx_penalty=20)
        assert s_no_pen > s_pen

    def test_btc_short_timeframe_weight_reduced(self):
        btc_1h = [_make_rsi_signal("BTC-USD", "1H", 65.0, "rising")]
        gold_1h = [_make_rsi_signal("GC=F", "1H", 65.0, "rising")]
        s_btc, _, _ = score_confidence(btc_1h, "bullish", 0.3, "FALLING", MacroFlags(), "BTC-USD")
        s_gold, _, _ = score_confidence(gold_1h, "bullish", 0.3, "FALLING", MacroFlags(), "GC=F")
        assert s_gold >= s_btc, "BTC 1H should have lower weight than gold 1H"

    def test_score_clamped_0_100(self):
        sigs = [_make_rsi_signal("GC=F", tf, 62.0, "rising") for tf in ("1H", "2H", "3H", "4H", "1D")]
        flags = MacroFlags(TREASURY_BUYBACK=True)
        score, _, _ = score_confidence(sigs, "bullish", 0.1, "FALLING", flags, "GC=F")
        assert 0 <= score <= 100


# ---------------------------------------------------------------------------
# sector_is_extended
# ---------------------------------------------------------------------------

class TestSectorExtended:
    def test_extended_at_top_decile(self):
        assert sector_is_extended("SMH", 0.91) is True
        assert sector_is_extended("KRE", 1.0) is True

    def test_not_extended_below_threshold(self):
        assert sector_is_extended("XHB", 0.89) is False
        assert sector_is_extended("XLU", 0.50) is False

    def test_none_percentile(self):
        assert sector_is_extended("SMH", None) is False


# ---------------------------------------------------------------------------
# MacroFlags strict separation
# ---------------------------------------------------------------------------

class TestMacroFlagSeparation:
    def test_buyback_not_labeled_qe(self):
        flags = MacroFlags(
            TREASURY_BUYBACK=True,
            treasury_buyback_detail=(
                "Treasury buyback active: 3 operations last 30d, ~$12.5B face value. "
                "Buying back long bonds via TGA cash. NOT Fed QE. Reversible."
            )
        )
        detail = flags.treasury_buyback_detail.lower()
        assert "not fed qe" in detail or "not qe" in detail, \
            "Treasury buyback detail MUST clarify it is NOT Fed QE"
        assert "reversible" in detail, \
            "Treasury buyback detail MUST note it is reversible"

    def test_foreign_holder_flow_independent(self):
        flags = MacroFlags(
            TREASURY_BUYBACK=False,
            FOREIGN_HOLDER_FLOW="SELLING",
        )
        assert flags.TREASURY_BUYBACK is False
        assert flags.FOREIGN_HOLDER_FLOW == "SELLING"

    def test_both_flags_can_coexist(self):
        flags = MacroFlags(TREASURY_BUYBACK=True, FOREIGN_HOLDER_FLOW="BUYING")
        assert flags.TREASURY_BUYBACK is True
        assert flags.FOREIGN_HOLDER_FLOW == "BUYING"


# ---------------------------------------------------------------------------
# Historical episode: 2018-12 → 2019-07 Fed pivot cycle
# Conditions: 10Y fell from 3.2% → 2.0%, real yield dropped, DXY fell,
#             2s10s steepened → gold LONG, GDX LONG, KRE bullish
# ---------------------------------------------------------------------------

class TestFedPivotCycle2019:
    """
    Simulate the 2018-12 → 2019-07 rate-cut-pivot episode.
    Fed signaled pivot in Jan 2019. 10Y dropped, real yields fell, DXY weakened.
    Expected: Gold LONG, GDX LONG (minus equity beta penalty), KRE positive (steepening curve).
    """

    def _pivot_signals(self, symbol: str) -> list[RSISignal]:
        if symbol == "GC=F":
            # 2019 pivot: momentum confirmed across 4 timeframes (realistic multi-TF scenario)
            return [_make_rsi_signal("GC=F", tf, rsi, "rising")
                    for tf, rsi in [("2H", 58), ("3H", 60), ("4H", 62), ("1D", 65)]]
        if symbol == "GDX":
            return [_make_rsi_signal("GDX", tf, rsi, "rising")
                    for tf, rsi in [("2H", 56), ("3H", 58), ("4H", 60), ("1D", 63)]]
        if symbol == "KRE":
            return [_make_rsi_signal("KRE", "1D", 55, "rising")]
        if symbol == "DX-Y.NYB":
            return [_make_rsi_signal("DX-Y.NYB", tf, rsi, "falling")
                    for tf, rsi in [("4H", 44), ("1D", 41)]]
        return []

    def test_gold_long_on_pivot(self):
        sigs = self._pivot_signals("GC=F")
        score, agreeing, _ = score_confidence(
            rsi_signals=sigs,
            target_direction="bullish",
            real_yield=0.15,    # real yield suppressed
            dxy_trend="FALLING",
            macro_flags=MacroFlags(),
            symbol="GC=F",
        )
        assert score >= CONFIDENCE_THRESHOLD, \
            f"Gold should be LONG on 2019 pivot. Score={score}"
        assert "1D" in agreeing or "4H" in agreeing

    def test_gdx_long_reduced_by_equity_beta(self):
        # In early 2019, equities also rebounded, so GDX penalty is minimal
        sigs = self._pivot_signals("GDX")
        score_no_pen, _, _ = score_confidence(
            sigs, "bullish", 0.15, "FALLING", MacroFlags(), "GDX", gdx_penalty=0
        )
        score_with_pen, _, _ = score_confidence(
            sigs, "bullish", 0.15, "FALLING", MacroFlags(), "GDX", gdx_penalty=8
        )
        assert score_with_pen < score_no_pen, "GDX penalty must reduce score"
        # With 4 timeframes confirming + real yield + DXY support, GDX stays bullish
        # even with an 8-pt equity-beta penalty (realistic: equities also rebounding in 2019)
        assert score_with_pen >= CONFIDENCE_THRESHOLD, \
            f"GDX LONG expected at/above threshold on 2019 pivot. Score={score_with_pen}"

    def test_kre_steepener_supported(self):
        sigs = self._pivot_signals("KRE")
        score, _, rationale = score_confidence(
            sigs, "bullish", None, "FLAT",
            MacroFlags(), "KRE", spread_trend="STEEPENING"
        )
        assert any("STEEPENING" in r for r in rationale), \
            "KRE steepening must be noted in rationale"

    def test_copper_regime_real_yield(self):
        # In 2019 pivot, copper was mixed while gold rose → safe-haven story
        gold_sigs = [_make_rsi_signal("GC=F", "1D", 65.0, "rising")]
        copper_sigs = [_make_rsi_signal("HG=F", "1D", 40.0, "falling")]
        regime = classify_copper_regime(gold_sigs, copper_sigs)
        assert regime == "REAL_YIELD_SAFE_HAVEN"


# ---------------------------------------------------------------------------
# Historical episode: 2020-03 COVID emergency cut
# Conditions: Fed cut 150bp to zero, 10Y collapsed to 0.5%, real yields went
#             deeply negative, DXY initially spiked on liquidity crunch then fell
#             → gold LONG (delayed), silver LONG, BTC volatile
# ---------------------------------------------------------------------------

class TestCovidEmergencyCut2020:
    """
    Simulate 2020-03 COVID emergency-cut episode.
    Phase 2 (Apr-May 2020): 10Y stable at 0.6%, real yield = -0.8%,
    DXY reversed and fell. Gold surged to ATH by Aug 2020.
    """

    def test_gold_long_on_negative_real_yield(self):
        # COVID Apr-May 2020: gold confirmed across 4 timeframes before ATH run
        sigs = [
            _make_rsi_signal("GC=F", "2H", 63.0, "rising"),
            _make_rsi_signal("GC=F", "3H", 65.0, "rising"),
            _make_rsi_signal("GC=F", "4H", 68.0, "rising"),
            _make_rsi_signal("GC=F", "1D", 70.0, "rising"),
        ]
        score, _, rationale = score_confidence(
            rsi_signals=sigs,
            target_direction="bullish",
            real_yield=-0.80,   # deeply negative real yield
            dxy_trend="FALLING",
            macro_flags=MacroFlags(),
            symbol="GC=F",
        )
        assert score >= CONFIDENCE_THRESHOLD, \
            f"Gold LONG expected on deeply negative real yield. Score={score}"
        assert any("real yield" in r.lower() for r in rationale)

    def test_silver_long_on_negative_real_yield(self):
        sigs = [
            _make_rsi_signal("SI=F", "2H", 58.0, "rising"),
            _make_rsi_signal("SI=F", "3H", 60.0, "rising"),
            _make_rsi_signal("SI=F", "4H", 62.0, "rising"),
            _make_rsi_signal("SI=F", "1D", 65.0, "rising"),
        ]
        score, _, _ = score_confidence(
            rsi_signals=sigs,
            target_direction="bullish",
            real_yield=-0.80,
            dxy_trend="FALLING",
            macro_flags=MacroFlags(),
            symbol="SI=F",
        )
        assert score >= CONFIDENCE_THRESHOLD, \
            f"Silver LONG expected on negative real yield. Score={score}"

    def test_btc_lower_weight_short_timeframes(self):
        # BTC surged in 2020 but its rate correlation on 1H-3H is noisy
        btc_sigs_short = [_make_rsi_signal("BTC-USD", tf, 65.0, "rising") for tf in ("1H", "2H", "3H")]
        gold_sigs_short = [_make_rsi_signal("GC=F", tf, 65.0, "rising") for tf in ("1H", "2H", "3H")]

        s_btc, _, _ = score_confidence(btc_sigs_short, "bullish", -0.8, "FALLING", MacroFlags(), "BTC-USD")
        s_gold, _, _ = score_confidence(gold_sigs_short, "bullish", -0.8, "FALLING", MacroFlags(), "GC=F")

        assert s_gold > s_btc, \
            "Gold must score higher than BTC on short timeframes only (BTC has noisy rate correlation)"

    def test_holding_period_position_on_structural(self):
        # Emergency cut = structural macro event → Position hold for gold
        flags = MacroFlags(TREASURY_BUYBACK=False, FOREIGN_HOLDER_FLOW="SELLING")
        hp = classify_holding_period(["4H", "1D"], flags)
        assert hp == "Position"

    def test_dxy_spike_headwind_then_reversal(self):
        # During liquidity crunch (March), DXY rising = headwind for gold
        sigs = [_make_rsi_signal("GC=F", "1D", 55.0, "rising")]
        s_dxy_rising, _, _ = score_confidence(
            sigs, "bullish", -0.5, "RISING", MacroFlags(), "GC=F"
        )
        # After reversal (April+), DXY falling = tailwind
        s_dxy_falling, _, _ = score_confidence(
            sigs, "bullish", -0.5, "FALLING", MacroFlags(), "GC=F"
        )
        assert s_dxy_falling > s_dxy_rising, \
            "DXY reversal from rising to falling must improve gold confidence"


# ---------------------------------------------------------------------------
# format_bdmc_report_section
# ---------------------------------------------------------------------------

class TestReportSection:
    def _make_scan_result(self, signals=None) -> BDMCScanResult:
        return BDMCScanResult(
            timestamp="2025-01-01T00:00:00+00:00",
            real_yield=0.35,
            nominal_10y=4.20,
            breakeven_10y=2.35,
            spread_2s10s=-0.15,
            spread_trend="FLATTENING",
            dxy_trend="RISING",
            vix_level=18.5,
            macro_flags=MacroFlags(
                TREASURY_BUYBACK=True,
                treasury_buyback_detail="TGA buyback $15B. NOT Fed QE. Reversible.",
                FOREIGN_HOLDER_FLOW="NEUTRAL",
            ),
            signals=signals or [],
            copper_regime="REAL_YIELD_SAFE_HAVEN",
        )

    def test_section_name(self):
        result = self._make_scan_result()
        section = format_bdmc_report_section(result)
        assert section["section"] == "Bond-Dollar-Cascade Read"

    def test_macro_context_present(self):
        result = self._make_scan_result()
        section = format_bdmc_report_section(result)
        ctx = section["macro_context"]
        assert ctx["real_yield"] == 0.35
        assert ctx["dxy_trend"] == "RISING"
        assert ctx["copper_regime"] == "REAL_YIELD_SAFE_HAVEN"

    def test_buyback_flag_not_labeled_qe(self):
        result = self._make_scan_result()
        section = format_bdmc_report_section(result)
        detail = section["macro_flags"]["treasury_buyback_detail"].lower()
        assert "not fed qe" in detail or "not qe" in detail

    def test_signals_serialized(self):
        sig = BDMCSignal(
            symbol="GC=F", label="Gold Futures",
            direction="LONG", confidence=75,
            holding_period="Swing", real_yield_driver=True,
            copper_regime="REAL_YIELD_SAFE_HAVEN",
            timeframes_agreeing=["4H", "1D"],
            rationale=["Timeframes agreeing: 4H, 1D"],
            macro_flags=MacroFlags(),
        )
        result = self._make_scan_result([sig])
        section = format_bdmc_report_section(result)
        assert section["signal_count"] == 1
        row = section["signals"][0]
        assert row["symbol"] == "GC=F"
        assert row["direction"] == "LONG"
        assert row["confidence"] == 75

    def test_threshold_documented(self):
        result = self._make_scan_result()
        section = format_bdmc_report_section(result)
        assert section["confidence_threshold"] == CONFIDENCE_THRESHOLD


class TestTelegramFormat:
    def test_buyback_warning_in_output(self):
        result = BDMCScanResult(
            timestamp="2025-01-01T00:00:00+00:00",
            real_yield=0.4,
            nominal_10y=4.1, breakeven_10y=2.2,
            spread_2s10s=0.2, spread_trend="STEEPENING",
            dxy_trend="FALLING", vix_level=15.0,
            macro_flags=MacroFlags(
                TREASURY_BUYBACK=True,
                treasury_buyback_detail="NOT Fed QE. Reversible.",
                FOREIGN_HOLDER_FLOW="NEUTRAL",
            ),
            signals=[],
            copper_regime="REAL_YIELD_SAFE_HAVEN",
        )
        text = format_bdmc_telegram(result)
        assert "TREASURY_BUYBACK" in text
        assert "NOT QE" in text or "NOT Fed QE" in text or "not qe" in text.lower()

    def test_foreign_selling_warning(self):
        result = BDMCScanResult(
            timestamp="2025-01-01T00:00:00+00:00",
            real_yield=0.4,
            nominal_10y=4.1, breakeven_10y=2.2,
            spread_2s10s=0.2, spread_trend="FLAT",
            dxy_trend="RISING", vix_level=20.0,
            macro_flags=MacroFlags(
                TREASURY_BUYBACK=False,
                FOREIGN_HOLDER_FLOW="SELLING",
            ),
            signals=[],
            copper_regime="UNKNOWN",
        )
        text = format_bdmc_telegram(result)
        assert "FOREIGN_HOLDER_FLOW" in text
        assert "SELLING" in text
        assert "dollar-NEGATIVE" in text or "dollar" in text.lower()

    def test_no_signals_message(self):
        result = BDMCScanResult(
            timestamp="2025-01-01T00:00:00+00:00",
            real_yield=1.8, nominal_10y=4.5, breakeven_10y=2.3,
            spread_2s10s=-0.1, spread_trend="FLATTENING",
            dxy_trend="RISING", vix_level=12.0,
            macro_flags=MacroFlags(), signals=[], copper_regime="UNKNOWN",
        )
        text = format_bdmc_telegram(result)
        assert "No cascade signals" in text or "threshold" in text.lower()


# ---------------------------------------------------------------------------
# confidence threshold enforcement
# ---------------------------------------------------------------------------

class TestConfidenceThreshold:
    def test_threshold_is_60(self):
        assert CONFIDENCE_THRESHOLD == 60

    def test_weak_signal_suppressed(self):
        # Single 1H timeframe, no real yield support, no DXY → should score below threshold
        sigs = [_make_rsi_signal("GC=F", "1H", 52.0, "rising")]
        score, _, _ = score_confidence(
            sigs, "bullish", 1.8, "RISING", MacroFlags(), "GC=F"
        )
        assert score < CONFIDENCE_THRESHOLD, \
            f"Weak single-timeframe signal should be suppressed. Score={score}"

    def test_build_signal_returns_none_below_threshold(self):
        # Single 1H with headwinds → below threshold → None
        sigs = [_make_rsi_signal("GC=F", "1H", 52.0, "rising")]
        result = build_signal(
            symbol="GC=F",
            rsi_signals=sigs,
            real_yield=2.0,
            dxy_trend="RISING",
            macro_flags=MacroFlags(),
            gdx_penalty=0,
            spread_trend="FLAT",
            vix=None,
            copper_regime="UNKNOWN",
        )
        assert result is None


# ---------------------------------------------------------------------------
# Integration: run_bdmc_scan with mocked network
# ---------------------------------------------------------------------------

class TestRunBdmcScanMocked:
    """
    Full integration test with all network calls mocked.
    Simulates a bullish gold environment and verifies scan produces expected output.
    """

    def _mock_ohlcv(self, symbol, period, interval):
        n = 50
        if symbol in ("GC=F", "SI=F", "GDX"):
            vals = [1800 + i * 5 for i in range(n)]
        elif symbol == "HG=F":
            vals = [4.2 - i * 0.01 for i in range(n)]  # copper falling
        elif symbol == "DX-Y.NYB":
            vals = [105 - i * 0.1 for i in range(n)]    # DXY falling
        elif symbol == "^VIX":
            vals = [18.0] * n
        elif symbol in ("SPY", "QQQ"):
            vals = [450 + i * 2 for i in range(n)]      # equities rising (no penalty)
        elif symbol in ("KRE", "XHB", "XLU", "SMH"):
            vals = [50 + i * 0.5 for i in range(n)]
        elif symbol in ("TNX", "FVX"):
            vals = [4.0 - i * 0.02 for i in range(n)]  # yields falling
        elif symbol in ("TLT", "SHY"):
            vals = [90 + i * 0.3 for i in range(n)]
        elif symbol == "BTC-USD":
            vals = [30000 + i * 100 for i in range(n)]
        else:
            vals = [100.0] * n
        return _make_price_series(vals)

    def _mock_fred(self, series_id):
        mapping = {
            "DGS10": 4.15, "T10YIE": 2.35,  # real yield = 1.8%
            "T10Y2Y": -0.10,
            UNIVERSE.get("BOGZ1FL263061705Q", "BOGZ1FL263061705Q"): None,
        }
        return mapping.get(series_id, None)

    def _mock_2s10s_trend(self):
        return -0.10, "FLATTENING"

    def _mock_dxy_trend(self):
        return 103.5, "FALLING"

    def _mock_vix(self):
        return 17.5

    def _mock_buyback(self):
        return MacroFlags(
            TREASURY_BUYBACK=False,
            treasury_buyback_detail="",
        )

    def _mock_foreign_flow(self):
        return "NEUTRAL"

    def test_scan_returns_bdmc_result(self):
        with patch("trading.bdmc_engine.fetch_yahoo_ohlcv", side_effect=self._mock_ohlcv), \
             patch("trading.bdmc_engine.fetch_real_yield", return_value=(1.80, 4.15, 2.35)), \
             patch("trading.bdmc_engine.fetch_2s10s_trend", side_effect=self._mock_2s10s_trend), \
             patch("trading.bdmc_engine.fetch_dxy_trend", side_effect=self._mock_dxy_trend), \
             patch("trading.bdmc_engine.fetch_vix", side_effect=self._mock_vix), \
             patch("trading.bdmc_engine.fetch_treasury_buyback_flag", side_effect=self._mock_buyback), \
             patch("trading.bdmc_engine.fetch_foreign_holder_flow", side_effect=self._mock_foreign_flow), \
             patch("trading.bdmc_engine.rsi_percentile_6m", return_value=0.50):

            result = run_bdmc_scan()

        assert isinstance(result, BDMCScanResult)
        assert result.timestamp is not None
        assert result.dxy_trend == "FALLING"
        assert result.spread_trend == "FLATTENING"
        # All signals must pass threshold
        for sig in result.signals:
            assert sig.confidence >= CONFIDENCE_THRESHOLD, \
                f"{sig.symbol} signal confidence {sig.confidence} below threshold"

    def test_report_section_structure(self):
        with patch("trading.bdmc_engine.fetch_yahoo_ohlcv", side_effect=self._mock_ohlcv), \
             patch("trading.bdmc_engine.fetch_real_yield", return_value=(1.80, 4.15, 2.35)), \
             patch("trading.bdmc_engine.fetch_2s10s_trend", side_effect=self._mock_2s10s_trend), \
             patch("trading.bdmc_engine.fetch_dxy_trend", side_effect=self._mock_dxy_trend), \
             patch("trading.bdmc_engine.fetch_vix", side_effect=self._mock_vix), \
             patch("trading.bdmc_engine.fetch_treasury_buyback_flag", side_effect=self._mock_buyback), \
             patch("trading.bdmc_engine.fetch_foreign_holder_flow", side_effect=self._mock_foreign_flow), \
             patch("trading.bdmc_engine.rsi_percentile_6m", return_value=0.50):

            result = run_bdmc_scan()

        section = format_bdmc_report_section(result)
        assert "section" in section
        assert "macro_context" in section
        assert "macro_flags" in section
        assert "signals" in section
        assert section["confidence_threshold"] == CONFIDENCE_THRESHOLD
