"""
conftest.py — stub out uninstallable C-extension / legacy packages so all tests
can be collected and run without the full production dependency stack.
"""
import sys
import types
import pandas as pd
import numpy as np
from unittest.mock import MagicMock


# ── feedparser stub ───────────────────────────────────────────────────────────

feedparser_mod = types.ModuleType("feedparser")

class _FeedResult:
    entries = []

feedparser_mod.parse = lambda url: _FeedResult()
sys.modules["feedparser"] = feedparser_mod


# ── ta (bukosabino/ta) stub ───────────────────────────────────────────────────
# Provides ta.momentum.RSIIndicator and ta.volume.AccDistIndexIndicator

def _rsi_series(series, n=14):
    """Pure-Python RSI so tests reflect real behaviour."""
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_g = gain.ewm(com=n - 1, min_periods=n).mean()
    avg_l = loss.ewm(com=n - 1, min_periods=n).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


class _RSIIndicator:
    def __init__(self, close, window=14, **kw):
        self._rsi = _rsi_series(close, window)

    def rsi(self):
        return self._rsi


class _ADIndicator:
    def __init__(self, high, low, close, volume, **kw):
        clv = ((close - low) - (high - close)) / (high - low).replace(0, np.nan)
        self._ad = (clv.fillna(0) * volume).cumsum()

    def acc_dist_index(self):
        return self._ad


momentum_mod = types.ModuleType("ta.momentum")
momentum_mod.RSIIndicator = _RSIIndicator

volume_mod = types.ModuleType("ta.volume")
volume_mod.AccDistIndexIndicator = _ADIndicator

ta_mod = types.ModuleType("ta")
ta_mod.momentum = momentum_mod
ta_mod.volume   = volume_mod

sys.modules["ta"]          = ta_mod
sys.modules["ta.momentum"] = momentum_mod
sys.modules["ta.volume"]   = volume_mod


# ── other optional stubs (only if not installed) ──────────────────────────────

for mod in ("anthropic", "yfinance", "reportlab", "openpyxl", "dateutil"):
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()
