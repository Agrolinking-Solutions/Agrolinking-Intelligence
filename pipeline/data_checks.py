"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Daily accuracy checks - is every commodity's data and forecast fit to show?

Runs after the forecasts and the cross-check, every day, for every commodity.
Each check gives ok, check (a person should look) or problem (likely wrong):

  data
    unit          latest real price vs the median of the 26 weeks before it -
                  a jump of 2x or more is usually a unit or basis error
                  (per kg vs per tonne, a bag size, a shop price)
    spikes        real prices in the last 3 years that jump 40%+ and fall
                  back next time - one wrong row, not a market move
    sources       two real sources a few weeks apart that disagree by 25%+
    basis         shop (retail) prices mixed into a wholesale series
    freshness     age of the latest real price
    filled_share  share of the last 26 weeks that are filled-in, not real
  forecast
    start         the forecast is built from the latest real price we hold
    sane          every horizon has a positive price, inside its range, and the
                  1-month move is within 3x the method's measured error
  accuracy
    tested        1-month accuracy on real prices (goal 95%), and the method
                  is not worse than "price holds"
    live          published forecasts scored against real prices that arrived
                  later; flagged when well below the tested accuracy
    cross_check   today's comparison with AFEX / web prices (16_cross_check)
  context
    weather       the commodity's weather card (weather_report.py) is from
                  today's MET Norway forecast and covers at least 5 days

Writes outputs/logs/data_checks.json (API /checks) and a table on the run
page. Report only: never fails the pipeline - the quality gate blocks
publishing for broken files.

Run: python pipeline/data_checks.py
"""

import os, sys, json, glob, math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS, COMMODITIES, REAL_SOURCES, ROOT

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

OUT_PATH      = os.path.join(PATHS["logs_dir"], "data_checks.json")
SHOP_PRICE_OK = {"Meat (beef)", "Meat (goat)", "Fish (dried)", "Eggs"}   # John, 14 Oct 2026: shop prices, labelled
RETIRED       = {"Rice"}           # legacy series kept until the frontend shows the two rice types
ACCURACY_GOAL = 95.0
RANK          = {"ok": 0, "check": 1, "problem": 2}


def result(name, status, message, **extra):
    return {"check": name, "status": status, "message": message, **extra}


# ── Data ─────────────────────────────────────────────────────────────────────

def check_unit(real):
    if len(real) < 4:
        return result("unit", "ok", "Too few real prices to compare")
    last = real.iloc[-1]
    prior = real[(real["date"] < last["date"]) & (real["date"] >= last["date"] - pd.Timedelta(weeks=26))]
    if len(prior) < 2:
        return result("unit", "ok", "No real prices in the 26 weeks before the latest one")
    ratio = last["price_ngn_mt"] / prior["price_ngn_mt"].median()
    msg = (f"Latest real price N{last['price_ngn_mt']:,.0f} ({last['date']:%d %b %Y}, {last['data_source']}) is "
           f"{ratio:.2f}x the median of the 26 weeks before")
    if ratio >= 2 or ratio <= 0.5:
        return result("unit", "problem", msg + " - check the unit (per kg / bag / tonne) or price type", ratio=round(ratio, 2))
    if ratio >= 1.5 or ratio <= 0.67:
        return result("unit", "check", msg, ratio=round(ratio, 2))
    return result("unit", "ok", msg, ratio=round(ratio, 2))


def check_spikes(real, today):
    r = real[real["date"] >= pd.Timestamp(today) - pd.Timedelta(weeks=156)].reset_index(drop=True)
    found = []
    for i in range(1, len(r) - 1):
        prev, cur, nxt = r.loc[i - 1], r.loc[i], r.loc[i + 1]
        if (cur["date"] - prev["date"]).days > 56 or (nxt["date"] - cur["date"]).days > 56:
            continue
        up, back = cur["price_ngn_mt"] / prev["price_ngn_mt"] - 1, nxt["price_ngn_mt"] / cur["price_ngn_mt"] - 1
        if abs(up) >= 0.4 and abs(back) >= 0.3 and (up > 0) != (back > 0):
            found.append(f"{cur['date']:%d %b %Y} N{cur['price_ngn_mt']:,.0f} ({cur['data_source']}, "
                         f"{up * 100:+.0f}% then {back * 100:+.0f}%)")
    if not found:
        return result("spikes", "ok", "No one-off jumps in the last 3 years")
    return result("spikes", "check", f"{len(found)} one-off jump(s) that reversed - likely a wrong row: "
                  + "; ".join(found[:3]) + (" ..." if len(found) > 3 else ""), rows=found)


def check_sources(real, today):
    r = real[real["date"] >= pd.Timestamp(today) - pd.Timedelta(weeks=156)]
    srcs = sorted(r["data_source"].unique())
    if len(srcs) < 2:
        return result("sources", "ok", f"One real source in the last 3 years ({srcs[0] if srcs else 'none'})")
    out, worst = [], "ok"
    for i, a in enumerate(srcs):
        for b in srcs[i + 1:]:
            sa, sb = r[r["data_source"] == a], r[r["data_source"] == b]
            ratios = []
            for _, row in sa.iterrows():
                near = sb[(sb["date"] - row["date"]).abs() <= pd.Timedelta(days=35)]
                if len(near):
                    ratios.append(row["price_ngn_mt"] / near["price_ngn_mt"].median())
            if len(ratios) < 3:
                continue
            med = float(np.median(ratios))
            status = "check" if (med >= 1.25 or med <= 0.8) else "ok"
            worst = max(worst, status, key=RANK.get)
            out.append(f"{a} vs {b}: {med:.2f}x ({len(ratios)} pairs)")
    if not out:
        return result("sources", "ok", "Real sources never a few weeks apart - nothing to compare")
    return result("sources", worst, "; ".join(out) + (" - more than 25% apart" if worst != "ok" else ""))


def check_basis(c, real, today):
    recent = real[real["date"] >= pd.Timestamp(today) - pd.Timedelta(weeks=104)]
    shop = recent[recent["market_type"].astype(str).str.lower() == "retail"]
    if shop.empty:
        return result("basis", "ok", "Wholesale or exchange prices only")
    if c in SHOP_PRICE_OK:
        return result("basis", "ok", "Shop (retail) prices - no wholesale source yet, labelled on the platform")
    return result("basis", "check", f"{len(shop)} of {len(recent)} real prices in the last 2 years are shop prices "
                  f"in a wholesale series ({', '.join(sorted(shop['data_source'].unique()))})")


def check_freshness(real, today):
    if real.empty:
        return result("freshness", "problem", "No real price at all")
    age = (pd.Timestamp(today) - real["date"].max()).days
    msg = f"Latest real price {real['date'].max():%d %b %Y} ({age} days, {real.iloc[-1]['data_source']})"
    return result("freshness", "ok" if age <= 45 else ("check" if age <= 120 else "problem"), msg, age_days=age)


def check_filled(rows, today):
    recent = rows[rows["date"] >= pd.Timestamp(today) - pd.Timedelta(weeks=26)]
    if recent.empty:
        return result("filled_share", "problem", "No prices in the last 26 weeks")
    share = (~recent["data_source"].isin(REAL_SOURCES)).mean() * 100
    # monthly sources (WFP) leave ~75% of weekly rows filled by design; flag under ~3 real prices in 26 weeks
    return result("filled_share", "ok" if share <= 88 else "check",
                  f"{share:.0f}% of the last 26 weeks are filled-in or estimated prices", pct=round(share, 1))


# ── Forecast ─────────────────────────────────────────────────────────────────

def finite(x):
    return isinstance(x, (int, float)) and math.isfinite(x)


def check_start(fc, rows):
    if not fc:
        return result("start", "problem", "Not in today's forecast file")
    start = fc.get("last_known_price")
    if not finite(start):
        return result("start", "problem", "Forecast has no starting price")
    if fc.get("last_real_price") is not None:
        # 05_forecast builds the forecast from the latest real price and carries it to this week
        real = rows[rows["data_source"].isin(REAL_SOURCES) & rows["price_ngn_mt"].gt(0)]
        if real.empty:
            return result("start", "problem", "Forecast says it is built from a real price, but none is held")
        last = real.iloc[-1]
        same = (abs(fc["last_real_price"] / last["price_ngn_mt"] - 1) <= 0.001
                and fc.get("last_real_date") == f"{last['date']:%Y-%m-%d}")
        moved = (start / fc["last_real_price"] - 1) * 100
        msg = (f"Built from the latest real price N{fc['last_real_price']:,.0f} ({fc['last_real_date']}, "
               f"{fc.get('last_real_source')})" + (f", carried to N{start:,.0f} on {fc.get('last_known_date')} "
               f"({moved:+.1f}%)" if fc.get("start_is_estimate") else ""))
        if not same:
            return result("start", "problem", msg + f" - but the latest real price held is N{last['price_ngn_mt']:,.0f} "
                          f"({last['date']:%Y-%m-%d})")
        return result("start", "ok", msg)
    held = rows[(rows["record_type"] != "forecast") & (rows["date"] <= pd.Timestamp(fc.get("last_known_date")))]
    if held.empty:
        return result("start", "problem", f"No price held on or before the forecast start ({fc.get('last_known_date')})")
    last = held.iloc[-1]
    gap = start / last["price_ngn_mt"] - 1
    msg = (f"Starts from N{start:,.0f} ({fc.get('last_known_date')}); price held then N{last['price_ngn_mt']:,.0f} "
           f"({last['date']:%Y-%m-%d}, {last['data_source']})")
    status = "ok" if abs(gap) <= 0.01 else ("check" if abs(gap) <= 0.05 else "problem")
    return result("start", status, msg + ("" if status == "ok" else f" - {gap * 100:+.1f}% apart"))


def check_sane(fc, sel):
    if not fc:
        return result("sane", "problem", "Not in today's forecast file")
    issues, start = [], fc.get("last_known_price") or 0
    for h, block in fc.get("horizons", {}).items():
        e = block.get("ensemble", {})
        v, lo, hi = e.get("values", []), e.get("lower_ci", []), e.get("upper_ci", [])
        if not v:
            issues.append(f"{h}: no values")
            continue
        if not all(finite(x) and x > 0 for x in v + lo + hi):
            issues.append(f"{h}: a price is missing, zero or negative")
        elif any(l > x * 1.0001 or x > u * 1.0001 for x, l, u in zip(v, lo, hi)):
            issues.append(f"{h}: the forecast falls outside its own range")
    if issues:
        return result("sane", "problem", "; ".join(issues))
    month = fc.get("horizons", {}).get("monthly", {}).get("ensemble", {}).get("values", [])
    if month and start:
        move = (month[-1] / start - 1) * 100
        err = 100 - (sel.get("accuracy_4w") or 90)
        limit = max(15.0, 3 * err)
        if abs(move) > limit:
            return result("sane", "check", f"1-month forecast moves {move:+.0f}%, more than 3x the method's "
                          f"measured 1-month error ({err:.1f}%)")
        return result("sane", "ok", f"All horizons valid; 1-month move {move:+.1f}%")
    return result("sane", "ok", "All horizons valid")


# ── Accuracy ─────────────────────────────────────────────────────────────────

def check_tested(sel):
    acc, naive = sel.get("accuracy_4w"), sel.get("naive_accuracy_4w")
    if acc is None:
        return result("tested", "check", f"Too few real prices to measure accuracy (n={sel.get('n_4w')})")
    fresh = sel.get("accuracy_4w_fresh_data")
    msg = (f"1-month accuracy {acc:.1f}% on real prices (n={sel.get('n_4w')}"
           + (f"; {fresh:.1f}% when the latest real price is under 4 weeks old" if fresh is not None else "")
           + f"), method {sel.get('method')}" + (f">{sel['long_method']}" if sel.get("long_method") else ""))
    if naive is not None and acc < naive - 0.5:
        return result("tested", "check", msg + f" - worse than 'price holds' ({naive:.1f}%)", accuracy=acc)
    status = "ok" if acc >= 85 else "check"
    return result("tested", status, msg + (" - meets the 95% goal" if acc >= ACCURACY_GOAL else ""), accuracy=acc)


def check_live(c, live, sel):
    """Judged on the current method only; the published record before 13 Oct 2026 is the old ensemble."""
    by = live.get("by_commodity", {}).get(c, {}).get("1 month", {}) or {}
    cur, old = by.get("champion") or {}, by.get("all") or {}
    if (cur.get("scored") or 0) < 6:
        if (old.get("scored") or 0) >= 6:
            return result("live", "ok", f"Current method not scored at 1 month yet; published forecasts so far "
                          f"(mostly the method used until 13 Oct 2026) {old['accuracy_pct']:.1f}% ({old['scored']} scored)")
        return result("live", "ok", "Too few published forecasts scored yet")
    acc, tested = cur["accuracy_pct"], sel.get("accuracy_4w")
    msg = f"Published 1-month forecasts {acc:.1f}% accurate ({cur['scored']} scored since {cur['first_forecast']})"
    if tested is not None and acc < tested - 5:
        return result("live", "check", msg + f" - below the tested {tested:.1f}%", accuracy=acc)
    return result("live", "ok", msg, accuracy=acc)


def check_cross(c, cross):
    e = cross.get("commodities", {}).get(c)
    if not e:
        return result("cross_check", "ok", "Not in today's cross-check")
    s = e["status"]
    if s == "disagree":
        return result("cross_check", "check", f"{e.get('compared_with')} is {e['gap_pct']:+.0f}% from ours - "
                      "a price-alert issue is open on GitHub")
    gap = f" ({e['gap_pct']:+.0f}%)" if e.get("gap_pct") is not None else ""
    return result("cross_check", "ok", f"{s}{gap}")


def check_weather(c, weather, today):
    card = (weather.get("commodities") or {}).get(c)
    if not card:
        return result("weather", "check", "No weather card in today's weather report")
    issued = str(weather.get("issued_at") or "")[:10]
    age = (pd.Timestamp(today) - pd.Timestamp(issued)).days if issued else None
    msg = f"{len(card['days'])}-day weather for {', '.join(card['areas'])}, forecast issued {issued or 'unknown'}"
    if age is None or age > 1 or len(card["days"]) < 5:
        return result("weather", "check", msg + " - the weather card is out of date or short")
    return result("weather", "ok", msg)


# ── Run ──────────────────────────────────────────────────────────────────────

def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def run_checks(today=None):
    today = today or datetime.now(timezone.utc).date()
    master = pd.read_csv(PATHS["master"], parse_dates=["date"]).sort_values("date")
    fc_path = sorted(glob.glob(os.path.join(ROOT, "outputs", "forecasts", "validated", "forecast_validated_*.json")))
    forecast = load_json(fc_path[-1], {}) if fc_path else {}
    selection = load_json(os.path.join(ROOT, "outputs", "backtest", "model_selection.json"), {})
    live = load_json(os.path.join(ROOT, "outputs", "backtest", "live_accuracy.json"), {})
    cross = load_json(os.path.join(PATHS["logs_dir"], "price_crosscheck.json"), {})
    wfiles = sorted(glob.glob(os.path.join(ROOT, "outputs", "intelligence", "weather_*.json")))
    weather = load_json(wfiles[-1], {}) if wfiles else {}

    report = {}
    for c in COMMODITIES:
        if c in RETIRED:
            continue
        rows = master[master["commodity"] == c]
        real = rows[rows["data_source"].isin(REAL_SOURCES) & rows["price_ngn_mt"].gt(0)].reset_index(drop=True)
        fc, sel = forecast.get(c), selection.get(c, {})
        checks = [check_unit(real), check_spikes(real, today), check_sources(real, today),
                  check_basis(c, real, today), check_freshness(real, today), check_filled(rows, today),
                  check_start(fc, rows), check_sane(fc, sel),
                  check_tested(sel), check_live(c, live, sel), check_cross(c, cross),
                  check_weather(c, weather, today)]
        worst = max((ch["status"] for ch in checks), key=RANK.get)
        report[c] = {"status": worst, "checks": checks}

    summary = {s: sum(v["status"] == s for v in report.values()) for s in RANK}
    tested = [next(ch for ch in v["checks"] if ch["check"] == "tested") for v in report.values()]
    accs = [t["accuracy"] for t in tested if t.get("accuracy") is not None]
    out = {"run_date": str(today), "summary": summary,
           "accuracy_1m_mean_pct": round(float(np.mean(accs)), 1) if accs else None,
           "commodities_at_goal": sum(a >= ACCURACY_GOAL for a in accs), "commodities_measured": len(accs),
           "statuses": {"ok": "fine", "check": "a person should look", "problem": "likely wrong"},
           "commodities": report}
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=2, default=str)
    return out


def log_and_summarise(out):
    logger.info("=" * 60)
    logger.info("DAILY ACCURACY CHECKS")
    logger.info("=" * 60)
    md = [f"### Accuracy checks ({out['run_date']})",
          f"{out['summary']['ok']} fine, {out['summary']['check']} to look at, {out['summary']['problem']} likely wrong. "
          f"Mean 1-month accuracy {out['accuracy_1m_mean_pct']}% on real prices; "
          f"{out['commodities_at_goal']}/{out['commodities_measured']} at the 95% goal.", "",
          "| Commodity | Status | What to look at |", "|---|---|---|"]
    for c, v in sorted(out["commodities"].items(), key=lambda kv: (-RANK[kv[1]["status"]], kv[0])):
        flagged = [ch for ch in v["checks"] if ch["status"] != "ok"]
        for ch in flagged:
            logger.log("WARNING" if ch["status"] == "problem" else "INFO",
                       f"  {ch['status']:<8} {c:<16} {ch['check']:<13} {ch['message']}")
        if flagged:
            md.append(f"| {c} | {v['status']} | " + "<br>".join(f"**{ch['check']}**: {ch['message']}" for ch in flagged) + " |")
    logger.info(f"  {out['summary']} | mean 1-month accuracy {out['accuracy_1m_mean_pct']}%")
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n" + "\n".join(md) + "\n")


if __name__ == "__main__":
    try:
        log_and_summarise(run_checks())
    except Exception as e:
        logger.warning(f"Accuracy checks failed this run (pipeline continues): {e}")
    sys.exit(0)
