import numpy as np
import pandas as pd
import pytest

from src.quant.screener import follow_through, screen_settings
from src.quant.setup_status import (
    AT_PIVOT,
    BREAKOUT_STATUS,
    INVALIDATED,
    WATCH,
    assess,
    entry_zone,
    market_note,
    rs_tier,
)

S = screen_settings()


@pytest.mark.parametrize("rs, tier", [(95, "STRONG_LEADER"), (90, "STRONG_LEADER"), (85, "LEADER"),
                                      (72, "QUALIFIED"), (60, None), (None, None)])
def test_rs_tiers(rs, tier):
    assert rs_tier(rs, S) == tier


@pytest.mark.parametrize("dist, zone", [(-12, "BELOW"), (-5, "IN_ZONE"), (0, "IN_ZONE"), (4.9, "IN_ZONE"),
                                        (6, "EXTENDED"), (None, None)])
def test_entry_zone(dist, zone):
    assert entry_zone(dist, S) == zone


def test_a_leader_far_below_its_pivot_is_watch_not_at_pivot():
    # "RS 94 + TT 8/8 + VCP" is not an entry when price is 12% below the pivot.
    a = assess("A", "VCP", -12.0, S, {"status": "UPTREND"})
    assert a["trend"] == "PASS" and a["setup"] == "PASS" and a["entry"] == "BELOW"
    assert a["status"] == WATCH


def test_all_three_questions_answered_gives_at_pivot():
    assert assess("B", "FLAT_BASE", -1.0, S, {"status": "UPTREND"})["status"] == AT_PIVOT


def test_no_base_never_reaches_at_pivot():
    assert assess("A", "NEAR_HIGH", -1.0, S)["status"] == WATCH


def test_most_recent_decisive_event_wins():
    after_failure = assess("A", "VCP", 1.0, S, recent_events=[("2026-09-22", "BREAKOUT"),
                                                               ("2026-09-24", "BREAKOUT_FAILED")])
    assert after_failure["status"] == INVALIDATED
    fresh = assess("A", "VCP", 1.0, S, recent_events=[("2026-09-20", "STOP_BREACHED"), ("2026-09-25", "BREAKOUT")])
    assert fresh["status"] == BREAKOUT_STATUS


def test_weak_breakout_does_not_count_as_breakout():
    assert assess("A", "VCP", 1.0, S, recent_events=[("2026-09-25", "BREAKOUT_WEAK")])["status"] == AT_PIVOT


def test_market_regime_switch():
    assert market_note({"status": "UPTREND"})[0] == "ok"
    assert market_note({"status": "UNDER_PRESSURE"})[0] == "caution"
    assert market_note({"status": "CORRECTION", "ftd": {}})[0] == "stop"
    assert market_note({"status": "CORRECTION", "ftd": {"date": "2026-09-20"}})[0] == "caution"
    assert market_note({"status": "CORRECTION", "ftd": {"date": "2026-08-03", "pressured": True,
                                                         "distribution_since": 6}})[0] == "stop"


# -- follow-through day -------------------------------------------------------------

def index_frame(closes, volumes):
    dates = pd.bdate_range(end="2026-09-25", periods=len(closes))
    c = np.asarray(closes, dtype="float64")
    return pd.DataFrame({"trade_date": [d.date().isoformat() for d in dates], "close": c,
                         "low": c * 0.998, "volume": np.asarray(volumes, dtype="float64")})


LEAD = [1000] * 6          # follow_through needs at least 10 sessions


def test_ftd_on_day_four_or_later_with_gain_and_volume():
    # low on the 4th decline, rally day 1 = first up close, FTD on rally day 4 (+2.3%, higher volume)
    closes = LEAD + [980, 960, 940, 945, 948, 950, 972]
    vols = [100] * (len(closes) - 1) + [150]
    f = index_frame(closes, vols)
    ftd = follow_through(f, S)
    assert ftd["rally_start"] == f["trade_date"].iloc[len(LEAD) + 3]
    assert ftd["date"] == f["trade_date"].iloc[-1]
    assert ftd["gain_pct"] >= S["market"]["ftd"]["min_gain_pct"]


def test_big_up_day_too_early_is_not_an_ftd():
    closes = LEAD + [980, 960, 940, 945, 970]                    # +2.6% on rally day 2
    ftd = follow_through(index_frame(closes, [100] * (len(closes) - 1) + [150]), S)
    assert ftd["date"] is None and ftd["rally_start"] is not None


def test_up_day_without_higher_volume_is_not_an_ftd():
    closes = LEAD + [980, 960, 940, 945, 948, 950, 972]
    ftd = follow_through(index_frame(closes, [100] * len(closes)), S)
    assert ftd["date"] is None


def test_undercutting_the_low_after_an_ftd_marks_it_failed():
    closes = LEAD + [980, 960, 940, 945, 948, 950, 972, 950, 930]
    vols = [100] * (len(LEAD) + 6) + [150, 100, 100]
    ftd = follow_through(index_frame(closes, vols), S)
    assert ftd["date"] is None and ftd["failed"] is True


# -- distribution-day expiry and the FTD state machine (congthuc.md section 11, 2026-10-07) ----

def bars_frame(bars):
    """bars: (close, volume[, low[, high]]); low / high default to +-0.2% of the close."""
    dates = pd.bdate_range(end="2026-09-25", periods=len(bars))
    rows = [{"trade_date": d.date().isoformat(), "close": float(b[0]), "volume": float(b[1]),
             "low": float(b[2] if len(b) > 2 else b[0] * 0.998), "high": float(b[3] if len(b) > 3 else b[0] * 1.002)}
            for d, b in zip(dates, bars)]
    return pd.DataFrame(rows)


def with_market(**changes):
    m = {**S["market"], **{k: v for k, v in changes.items() if k != "ftd"}}
    if "ftd" in changes:
        m["ftd"] = {**S["market"]["ftd"], **changes["ftd"]}
    return {**S, "market": m}


def test_a_distribution_day_stops_counting_after_25_sessions():
    from src.quant.screener import active_distribution_count, distribution_expiry

    f = bars_frame([(1000, 100)] * 10 + [(990, 200)] + [(990, 100)] * 30)     # one distribution day, row 10
    assert distribution_expiry(f, S) == {10: 35}
    counts = active_distribution_count(f, S)
    assert counts.iloc[34] == 1 and counts.iloc[35] == 0


def test_a_5pct_rally_from_its_close_removes_a_distribution_day():
    from src.quant.screener import distribution_expiry

    # row 15 trades 5% above the 990 close of row 10 (high 1,040) but closes at 1,000
    f = bars_frame([(1000, 100)] * 10 + [(990, 200)] + [(990, 100)] * 4 + [(1000, 100, 998, 1040)] + [(1000, 100)] * 10)
    assert distribution_expiry(f, S) == {10: 15}                                          # on the session high
    assert distribution_expiry(f, with_market(distribution_expire_on="close")) == {10: 26}  # closes only: still counted


def ftd_then_distribution():
    """Decline to 900, FTD on rally day 4, a slow rise, then five distribution days."""
    b = [(1000, 100)] * 6 + [(980, 100), (960, 100), (940, 100), (900, 100)]
    b += [(905, 100), (910, 100), (915, 100), (935, 150)]
    for _ in range(5):
        b.append((b[-1][0] * 1.004, 100))
    for _ in range(5):
        b += [(b[-1][0] * 0.995, 200), (b[-1][0] * 0.995 * 1.002, 100)]
    return b


def test_five_distribution_days_end_an_ftd_and_a_new_cycle_starts_there():
    r = follow_through(bars_frame(ftd_then_distribution()), S)
    assert r["date"] is None and r["pressured"] is True
    assert r["last_ftd"]["outcome"] == "invalidated" and r["last_ftd"]["distribution_since"] == 5
    assert market_note({"status": "CORRECTION", "ftd": r})[0] == "stop"


def test_a_later_correction_with_a_higher_low_still_gets_its_ftd():
    # The old reading kept the 90-session low (900) as the reference and never saw this rally.
    b = ftd_then_distribution()
    lvl = b[-1][0]
    b += [(lvl * 0.99, 100), (lvl * 0.98, 100), (lvl * 0.97, 100)]
    b += [(lvl * 0.975, 100), (lvl * 0.98, 100), (lvl * 0.985, 100), (lvl * 0.985 * 1.02, 160)]
    f = bars_frame(b)
    r = follow_through(f, S)
    assert r["state"] == "FTD" and r["date"] == f["trade_date"].iloc[-1] and min(x[0] for x in b) == 900


def test_an_ftd_fails_when_the_session_low_undercuts_the_rally_low():
    b = [(1000, 100)] * 6 + [(980, 100), (960, 100), (940, 100), (900, 100), (905, 100), (910, 100), (915, 100), (935, 150)]
    b.append((920, 100, 895))                  # low 895 under the rally low (898.2); the close is not
    f = bars_frame(b)
    r = follow_through(f, S)
    assert r["state"] == "WAIT_LOW" and r["failed"] is True and r["last_ftd"]["outcome"] == "failed"
    assert follow_through(f, with_market(ftd={"undercut_on": "close"}))["state"] == "FTD"
