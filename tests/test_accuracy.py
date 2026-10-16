"""
Accuracy tests - the rules that keep prices and forecasts honest, and the
data itself. Run daily by the pipeline (step "Tests") and locally with:

    pytest tests/ -v

Rule tests use small made-up data. Data tests read the current master
dataset and fail when a known class of error comes back (CFA-franc border
prices, the duplicated WFP extract, future-dated or non-positive real prices).
"""

import os, sys, importlib.util

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from config.settings import PATHS, COMMODITIES, REAL_SOURCES, PARTNER_SOURCES


def load(step):
    """Import a pipeline step whose file name starts with a number."""
    spec = importlib.util.spec_from_file_location(step, os.path.join(ROOT, "pipeline", f"{step}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Forecast ranges ──────────────────────────────────────────────────────────

def test_range_widens_with_horizon_and_data_age():
    from pipeline.drift_models import range_halfwidth
    weeks = np.array([4, 8, 13, 26])
    fresh = range_halfwidth(np.full(4, 8.0), weeks, 0)
    old = range_halfwidth(np.full(4, 8.0), weeks, 20)
    assert np.all(np.diff(fresh) > 0), "same error further out must give a wider range"
    assert np.all(old > fresh), "old data must give a wider range"
    assert fresh[0] == pytest.approx(0.08 * 1.69), "1-month fresh range = 1.69 x measured error"


def test_range_never_narrower_further_out():
    from pipeline.drift_models import range_halfwidth
    weeks = np.arange(1, 27)
    err = np.interp(weeks, [4, 8, 26], [2.8, 3.0, 4.3])   # Wheat, 26 weeks without a real price
    hw = range_halfwidth(err, weeks, 26)
    assert np.all(np.diff(hw) >= 0)


def test_range_has_floor_and_cap():
    from pipeline.drift_models import range_halfwidth, RANGE_MAX
    assert range_halfwidth(0.1, 4, 0) == pytest.approx(0.02 * 1.69), "error floor of 2%"
    assert range_halfwidth(90.0, 26, 52) == pytest.approx(RANGE_MAX)


# ── Method selection ─────────────────────────────────────────────────────────

def summ_from(mapes):
    """{method: (m4, m8, m13, m26)} -> the summary table 13_select_forecaster scores on."""
    rows = [{"method": m, "h": h, "mape": v, "n": 30}
            for m, vals in mapes.items() for h, v in zip((4, 8, 13, 26), vals)]
    return pd.DataFrame(rows)


def test_selection_keeps_price_holds_unless_clearly_better():
    sel = load("13_select_forecaster")
    base = {m: (5.0, 7.0, 9.0, 14.0) for m in sel.CANDIDATES}
    best, _, _ = sel.choose(summ_from(base))
    assert best in ("naive", sel.TIE_METHOD)
    better = dict(base, **{"season@1.0": (4.0, 5.5, 7.0, 10.0)})
    best, _, _ = sel.choose(summ_from(better))
    assert best == "season@1.0"


def test_long_range_method_never_naive_and_needs_a_real_gain():
    sel = load("13_select_forecaster")
    base = {m: (5.0, 7.0, 9.0, 14.0) for m in sel.CANDIDATES}
    assert sel.choose_long(summ_from(base), "naive") is None
    gain = dict(base, **{"trend@0.5": (5.5, 7.5, 9.0, 11.0)})
    assert sel.choose_long(summ_from(gain), "naive") == "trend@0.5"


# ── Cross-check and alerts ───────────────────────────────────────────────────

def test_cross_check_thresholds():
    cc = load("16_cross_check")
    assert cc.status_for(0.10) == "agree"
    assert cc.status_for(-0.20) == "check"
    assert cc.status_for(0.31) == "disagree"


def test_afex_ginger_is_listed_not_compared(tmp_path):
    cc = load("16_cross_check")
    p = tmp_path / "afex.csv"
    pd.DataFrame([{"as_of_date": "2026-10-15", "mansa_commodity": "ginger", "country": "Nigeria",
                   "price_type": "exchange", "price_ngn_mt": 990000.0, "price_as_shown": "990 NGN / kg",
                   "source": "AFEX", "url": "https://www.mansamarkets.com/commodities/ginger",
                   "retrieved_on": "2026-10-16"}]).to_csv(p, index=False)
    cands = cc.afex_candidates(str(p))
    assert cands and all(c["price_level"] not in cc.COMPARE_LEVELS for c in cands)


def alert_report(gap, status="disagree"):
    e = {"status": status, "gap_pct": gap, "our_price": 1_400_000, "our_date": "2026-10-12",
         "our_source": "Agricome", "our_level": "wholesale", "web_median": 2_300_000,
         "compared_with": "AFEX exchange", "notes": [], "other_web_prices": [],
         "web_prices": [{"price": 2_300_000, "site": "mansamarkets.com", "url": "https://x",
                         "source_date": "2026-10-15", "level": "exchange"}]}
    return {"run_date": "2026-10-16", "commodities": {"Sesame": e}}


def test_alerts_open_stay_quiet_email_and_close():
    pa = load("17_price_alerts")
    issues = []

    def run(rep):
        acts = pa.plan(rep, issues)
        for a in acts:
            if a["do"] == "open":
                issues.append({"number": 1, "body": a["body"]})
            elif a["do"] in ("comment", "update"):
                issues[0]["body"] = a["body"]
            elif a["do"] == "close":
                issues.clear()
        return [a["do"] for a in acts]

    assert run(alert_report(64.3)) == ["open"]
    assert run(alert_report(64.3)) == ["update"]          # same gap: no email
    assert run(alert_report(70.0)) == ["update"]          # moved < 10 points: no email
    assert run(alert_report(80.0)) == ["comment"]         # moved 10+ points: email
    assert run(alert_report(None, "no web price")) == []  # nothing to compare: leave it
    assert run(alert_report(28.0, "check")) == []         # under 30% but not under 25%: stays open
    assert run(alert_report(-20.0, "check")) == ["close"]
    assert run(alert_report(40.0)) == ["open"]


# ── Daily data checks ────────────────────────────────────────────────────────

def series(prices, source="Agricome", start="2026-01-05", market="wholesale"):
    d = pd.date_range(start, periods=len(prices), freq="W-MON")
    return pd.DataFrame({"date": d, "price_ngn_mt": prices, "data_source": source, "market_type": market})


def test_unit_error_is_a_problem():
    dc = load("data_checks")
    ok = series([500_000] * 10)
    per_kg_typo = series([500_000] * 9 + [500_000_000])
    assert dc.check_unit(ok)["status"] == "ok"
    assert dc.check_unit(per_kg_typo)["status"] == "problem"


def test_one_off_spike_is_flagged():
    dc = load("data_checks")
    s = series([500_000, 510_000, 900_000, 515_000, 520_000])
    assert dc.check_spikes(s, pd.Timestamp("2026-03-01").date())["status"] == "check"


def test_shop_prices_in_a_wholesale_series_are_flagged():
    dc = load("data_checks")
    mixed = pd.concat([series([500_000] * 4), series([550_000] * 4, start="2026-03-02", market="retail")])
    today = pd.Timestamp("2026-06-01").date()
    assert dc.check_basis("Beans (red)", mixed, today)["status"] == "check"
    assert dc.check_basis("Eggs", mixed, today)["status"] == "ok"   # shop prices allowed, labelled


def test_forecast_outside_its_range_is_a_problem():
    dc = load("data_checks")
    fc = {"last_known_price": 100.0, "horizons": {"monthly": {"ensemble": {
        "values": [101.0, 130.0], "lower_ci": [95.0, 96.0], "upper_ci": [105.0, 110.0]}}}}
    assert dc.check_sane(fc, {"accuracy_4w": 92})["status"] == "problem"


# ── Ingest rules ─────────────────────────────────────────────────────────────

def test_older_wfp_extract_is_not_counted_twice():
    ing = load("01_ingest")
    months = pd.date_range("2020-01-06", periods=24, freq="MS") + pd.offsets.Week(weekday=0)
    current = pd.DataFrame({"commodity": "Beans (red)", "date": months[:20], "price_ngn_mt": 1000.0,
                            "data_source": "WFP"})
    legacy = pd.DataFrame({"commodity": "Beans (red)", "date": months + pd.Timedelta(days=7),
                           "price_ngn_mt": 1080.0, "data_source": "Agricome", "source": ing.LEGACY_WFP_SOURCE,
                           "market_type": "retail", "notes": ""})
    out = ing.place_legacy_wfp(legacy, current)
    assert len(out) == 4, "only the 4 months the current data lacks are kept"
    assert (out["data_source"] == "WFP").all()
    assert out["price_ngn_mt"].round(6).eq(1000.0).all(), "scaled to the current level"


# ── The data itself ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def master():
    if not os.path.exists(PATHS["master"]):
        pytest.skip("no master dataset")
    return pd.read_csv(PATHS["master"], parse_dates=["date"], low_memory=False)


def test_one_row_per_commodity_and_week(master):
    assert not master.duplicated(["commodity", "date"]).any()


def test_real_prices_positive_and_not_in_the_future(master):
    real = master[master["data_source"].isin(REAL_SOURCES)]
    assert (real["price_ngn_mt"] > 0).all()
    assert real["date"].max() <= pd.Timestamp.today().normalize() + pd.Timedelta(days=7)


def test_every_commodity_has_real_prices(master):
    have = set(master.loc[master["data_source"].isin(PARTNER_SOURCES), "commodity"])
    assert set(COMMODITIES) <= have, f"no partner price at all: {set(COMMODITIES) - have}"


def test_no_older_wfp_extract_labelled_agricome(master):
    bad = master[(master["data_source"] == "Agricome") & (master["source"].astype(str) == "WFP Nigeria Food Prices")]
    assert bad.empty, f"{len(bad)} WFP rows labelled Agricome"


def test_no_cfa_franc_border_prices(master):
    """Pre-2016 white maize ran ~N60K/t at Kano; CFA-franc border rows doubled it."""
    mz = master[(master["commodity"] == "Maize (white)") & master["data_source"].isin(PARTNER_SOURCES)
                & (master["date"].dt.year == 2005)]
    if mz.empty:
        pytest.skip("no 2005 white maize")
    assert mz["price_ngn_mt"].median() < 100_000
