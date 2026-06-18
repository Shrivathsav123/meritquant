"""Tests for macro.py — environment detection and scoring thresholds."""
import pytest
from unittest.mock import patch

from macro import get_macro_environment, format_macro_alert, get_fred_data


# ── Helpers ───────────────────────────────────────────────────────────────────

def run_macro(vix=None, dxy_chg=None, tlt_chg=None, tnx_chg=None,
              cpi=None, cpi_prev=None, fed=None):
    """
    Run get_macro_environment with controlled fetch_yahoo_quote and FRED responses.
    Returns the macro dict.
    """
    def fake_yahoo_quote(symbol):
        if "VIX" in symbol:
            return (vix, None, 0.0) if vix else (None, None, None)
        if "DX-Y" in symbol:
            return (100.0, None, dxy_chg) if dxy_chg is not None else (None, None, None)
        if symbol in ("TLT",):
            return (90.0, None, tlt_chg) if tlt_chg is not None else (None, None, None)
        if symbol in ("EDV", "IEV"):
            return (50.0, None, 0.0)
        if "TNX" in symbol:
            return (45.0, None, tnx_chg) if tnx_chg is not None else (None, None, None)
        return (None, None, None)

    def fake_fred(series_id):
        if series_id == "CPIAUCSL":
            return (cpi, cpi_prev) if cpi else (None, None)
        if series_id == "FEDFUNDS":
            return (fed, None) if fed else (None, None)
        return (None, None)

    with patch("macro.fetch_yahoo_quote", side_effect=fake_yahoo_quote), \
         patch("macro.get_fred_data", side_effect=fake_fred), \
         patch("macro.feedparser.parse", return_value=type("F", (), {"entries": []})()) :
        return get_macro_environment()


# ── VIX Thresholds ────────────────────────────────────────────────────────────

class TestVixSignals:
    def test_vix_below_15_is_risk_on(self):
        macro = run_macro(vix=12.0)
        assert macro["vix"]["signal"] == "✅ Very low fear — Risk ON"

    def test_vix_between_15_and_20_is_mild_risk_on(self):
        macro = run_macro(vix=17.0)
        assert "Calm" in macro["vix"]["signal"]

    def test_vix_between_20_and_30_is_caution(self):
        macro = run_macro(vix=25.0)
        assert "Caution" in macro["vix"]["signal"]

    def test_vix_above_30_is_risk_off(self):
        macro = run_macro(vix=35.0)
        assert "Risk OFF" in macro["vix"]["signal"]

    def test_vix_spike_adds_alert(self):
        with patch("macro.fetch_yahoo_quote") as mock_q, \
             patch("macro.get_fred_data", return_value=(None, None)), \
             patch("macro.feedparser.parse", return_value=type("F", (), {"entries": []})()) :
            # Return vix_chg > 15
            def side(symbol):
                if "VIX" in symbol:
                    return (35.0, 30.0, 16.0)
                return (None, None, None)
            mock_q.side_effect = side
            macro = get_macro_environment()
            assert any("VIX SPIKE" in a or "VIX SURGING" in a for a in macro["alerts"])

    def test_unavailable_vix_does_not_crash(self):
        macro = run_macro(vix=None)
        assert macro["vix"]["value"] == "N/A"


# ── DXY Thresholds ────────────────────────────────────────────────────────────

class TestDxySignals:
    def test_falling_dxy_is_bullish(self):
        macro = run_macro(vix=18.0, dxy_chg=-0.5)
        assert "Bullish" in macro["dxy"]["signal"] or "Falling" in macro["dxy"]["signal"]

    def test_rising_dxy_is_headwind(self):
        macro = run_macro(vix=18.0, dxy_chg=0.5)
        assert "Headwind" in macro["dxy"]["signal"] or "Rising" in macro["dxy"]["signal"]

    def test_stable_dxy_is_neutral(self):
        macro = run_macro(vix=18.0, dxy_chg=0.1)
        assert "Stable" in macro["dxy"]["signal"]


# ── Overall Environment ───────────────────────────────────────────────────────

class TestEnvironmentDetermination:
    def test_strong_bullish_signals_give_risk_on(self):
        # VIX < 15 = +2 bull; DXY falling = +1 bull; total net >= 3
        macro = run_macro(vix=12.0, dxy_chg=-0.5)
        assert "RISK ON" in macro["environment"] or "BULLISH" in macro["environment"]

    def test_strong_bearish_signals_give_risk_off(self):
        # VIX > 30 = +2 bear; DXY rising = +1 bear; total net <= -3
        macro = run_macro(vix=35.0, dxy_chg=0.5, tlt_chg=-1.0, tnx_chg=2.0)
        assert "RISK OFF" in macro["environment"] or "CAUTIOUS" in macro["environment"]

    def test_neutral_with_no_signals(self):
        # No data = no bullish/bearish counts → net = 0 → NEUTRAL
        macro = run_macro()
        assert "NEUTRAL" in macro["environment"]

    def test_score_modifier_positive_in_bullish_env(self):
        macro = run_macro(vix=10.0, dxy_chg=-0.5)
        assert macro["score_modifier"] >= 0

    def test_score_modifier_negative_in_bearish_env(self):
        macro = run_macro(vix=40.0, dxy_chg=0.8, tlt_chg=-1.0, tnx_chg=2.0)
        assert macro["score_modifier"] <= 0

    def test_environment_key_always_present(self):
        macro = run_macro()
        assert "environment" in macro
        assert "score_modifier" in macro

    def test_required_keys_present(self):
        macro = run_macro()
        for key in ["vix", "dxy", "bonds", "environment", "score_modifier", "alerts", "summary"]:
            assert key in macro


# ── FRED ──────────────────────────────────────────────────────────────────────

class TestFredData:
    def test_no_api_key_returns_none(self):
        with patch("macro.FRED_API_KEY", ""):
            v1, v2 = get_fred_data("CPIAUCSL")
            assert v1 is None
            assert v2 is None

    def test_cpi_rising_adds_bearish_alert(self):
        # CPI 320 vs prev 319 → > 0.3% change → bearish
        macro = run_macro(vix=18.0, cpi=320.0, cpi_prev=319.0)
        assert any("CPI" in a for a in macro["alerts"])

    def test_high_fed_rate_adds_bearish_count(self):
        # fed > 5.0 → bearish
        macro = run_macro(vix=18.0, fed=5.5)
        # environment should be cautious/neutral rather than strongly bullish
        assert "RISK ON" not in macro["environment"] or macro["score_modifier"] <= 0


# ── format_macro_alert ────────────────────────────────────────────────────────

class TestFormatMacroAlert:
    def test_output_is_string(self):
        macro = run_macro(vix=18.0)
        result = format_macro_alert(macro)
        assert isinstance(result, str)

    def test_contains_environment_label(self):
        macro = run_macro(vix=18.0)
        result = format_macro_alert(macro)
        assert macro["environment"] in result

    def test_contains_vix_value(self):
        macro = run_macro(vix=18.0)
        result = format_macro_alert(macro)
        assert "18" in result

    def test_handles_missing_fields_gracefully(self):
        minimal = {
            "environment": "NEUTRAL ⚪",
            "score_modifier": 0,
            "vix": {}, "dxy": {}, "bonds": {}, "bond_etfs": {},
            "fred": {}, "fed_news": [], "alerts": [],
        }
        result = format_macro_alert(minimal)
        assert isinstance(result, str)
