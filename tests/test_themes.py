import numpy as np
import pandas as pd

from src.quant.screener import screen_settings
from src.quant.themes import load_themes, theme_strength, themes_of

S = screen_settings()


def _frame(start, end, n=120):
    c = np.linspace(start, end, n)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1e6},
                        index=pd.bdate_range("2026-03-02", periods=n))


def test_a_strong_theme_is_hot_and_ranked_against_industries():
    frames = {"UP1": _frame(10, 20), "UP2": _frame(10, 18), "DN1": _frame(20, 12), "DN2": _frame(20, 14)}
    raw = {"UP1": 0.9, "UP2": 0.8, "DN1": -0.4, "DN2": -0.3}
    themes = {"Sóng": ["UP1", "UP2", "ILLIQ"], "Yếu": ["DN1", "DN2"]}
    industry_means = {"a": 0.1, "b": 0.0, "c": -0.1, "d": 0.2}
    stats = theme_strength(themes, frames, raw, {"UP1": "A"}, industry_means, S)
    hot, weak = stats["Sóng"], stats["Yếu"]
    assert hot.members == ["UP1", "UP2"]                   # ILLIQ never cleared the floor
    assert hot.hot and not weak.hot and hot.rs_percentile > weak.rs_percentile
    assert hot.return_1m > 0 > weak.return_1m and hot.above_ma50_pct == 100.0
    assert hot.leaders == ["UP1"]
    assert themes_of("UP2", stats) == [{"name": "Sóng", "rs_percentile": hot.rs_percentile, "hot": True}]


def test_a_theme_with_too_few_liquid_members_is_not_ranked():
    stats = theme_strength({"Một mã": ["UP1"]}, {"UP1": _frame(10, 20)}, {"UP1": 0.9}, {}, {"a": 0.1}, S)
    assert stats["Một mã"].rs_percentile is None and not stats["Một mã"].hot


def test_the_config_file_declares_the_users_themes():
    themes = load_themes()
    assert {"Dầu khí", "Cao su", "Cảng & vận tải biển"} <= set(themes)
    assert "BSR" in themes["Dầu khí"] and "GVR" in themes["Cao su"] and "GMD" in themes["Cảng & vận tải biển"]
