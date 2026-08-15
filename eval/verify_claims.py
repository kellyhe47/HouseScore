#!/usr/bin/env python3
"""Re-derive every golden fixture's arithmetic from the R6 scoring rules.

Run: python3 eval/verify_claims.py
Exits non-zero if any fixture's expected score doesn't reproduce, so a later
agent can trust the spec is still internally consistent instead of trusting us.
"""
import json, sys
from datetime import date
from pathlib import Path

GOLDEN = Path(__file__).parent / "golden"

def parse_iso(s):
    return date.fromisoformat(s) if s else None

def parse_deed(raw, today):
    """A fixture's DEED_DATE, in either shape the real normalizer accepts.

    MOD-IV and SR1A both write raw YYMMDD; fixtures written by hand use ISO.
    The shipped `normalize.parse_deed_date` takes both, so this stdlib
    re-derivation has to as well — otherwise a fixture carrying the authentic
    raw shape could not be checked here at all.
    """
    if raw and len(str(raw)) == 6 and str(raw).isdigit():
        return parse_yymmdd(str(raw), today)
    return parse_iso(raw)

def parse_yymmdd(raw, today):
    if not raw or len(raw) != 6 or not raw.isdigit():
        return None
    yy, mm, dd = int(raw[:2]), int(raw[2:4]), int(raw[4:6])
    pivot = (today.year % 100) + 1
    year = 2000 + yy if yy <= pivot else 1900 + yy
    try:
        return date(year, mm, dd)
    except ValueError:
        return None

def score(given):
    cfg = given["config"]
    p = given["parcel"]
    as_of = parse_iso(given["as_of"])
    total = 0
    # Mover (R6): bands 30/60/90 days; non-arm's-length earns 0
    deed = parse_deed(p["DEED_DATE"], as_of) if p.get("DEED_DATE") else None
    nominal = (p.get("SALE_PRICE") or 0) <= 100 or (p.get("SALES_CODE") or "") != ""
    if deed and not nominal:
        d = (as_of - deed).days
        total += 100 if d <= 30 else 85 if d <= 60 else 70 if d <= 90 else 0
    # Hires-out: 20/permit cap 40; churn +20 iff >=2 distinct contractors, no repeats
    permits = given.get("permits_2yr", [])
    total += min(len(permits) * 20, 40)
    names = [x["contractor"] for x in permits if x.get("contractor")]
    if len(set(names)) >= 2 and len(set(names)) == len(names):
        total += 20
    # Capacity: median 15 (1.5x -> 25); ACS prior +5
    med = cfg["territory_median_value"]
    nv = p.get("NET_VALUE") or 0
    if nv >= 1.5 * med:
        total += 25
    elif nv >= med:
        total += 15
    # An absent block group is the ordinary case, not a malformed fixture: the
    # live run has no CENSUS_API_KEY and every door resolves without ACS. R6.2's
    # nudge simply does not apply, exactly as the engine skips it on a None.
    dual = (given.get("acs_block_group") or {}).get("dual_income_pct")
    if dual is not None and dual >= cfg["acs_dual_income_threshold"]:
        total += 5
    # Need: age 8, pool 8, lot 4, decline 8, deferred combo 4
    v = given.get("vision", {})
    yr = p.get("YR_CONSTR") or 0
    old = yr > 0 and (as_of.year - yr) >= 30
    order = ["poor", "fair", "good", "excellent"]
    decline = ("condition_2015" in v and "condition_2020" in v
               and order.index(v["condition_2020"]) < order.index(v["condition_2015"]))
    total += 8 * old + 8 * bool(v.get("pool")) + 4 * ((p.get("CALC_ACRE") or 0) >= 0.5)
    total += 8 * decline + 4 * (old and not permits and decline)
    # Modifier
    if given.get("rental_registration_match"):
        total -= 15
    return max(0, min(100, total))

def main():
    failures = []
    for f in sorted(GOLDEN.glob("*.json")):
        fx = json.loads(f.read_text())
        name = fx["name"]
        if name == "vision_eval_contract":
            c = fx["given"]["labeled_samples"]["predictions"]
            u = fx["given"]["labeled_samples"]["universe"]
            if c["true_positive"] + c["false_positive"] + c["false_negative"] + c["true_negative"] != u:
                failures.append(f"{name}: confusion counts don't sum to universe {u}")
            prec = c["true_positive"] / (c["true_positive"] + c["false_positive"])
            rec = c["true_positive"] / (c["true_positive"] + c["false_negative"])
            if abs(prec - fx["expect"]["precision"]) > 0.001:
                failures.append(f"{name}: precision {prec:.4f} != {fx['expect']['precision']}")
            if abs(rec - fx["expect"]["recall"]) > 0.001:
                failures.append(f"{name}: recall {rec:.4f} != {fx['expect']['recall']}")
            h = fx["given"]["hallucination_probe"]
            if h["claimed_detections"] / h["universe"] != fx["expect"]["hallucination_rate"]:
                failures.append(f"{name}: hallucination rate mismatch")
            continue
        if name == "deed_date_yymmdd_parse":
            today = date(2026, 8, 1)
            for case in fx["given"]["cases"]:
                got = parse_yymmdd(case["raw"], today)
                want = parse_iso(case["expect_iso"]) if case["expect_iso"] else None
                if got != want:
                    failures.append(f"{name}: '{case['raw']}' -> {got}, expected {want}")
            continue
        got = score(fx["given"])
        want = fx["expect"]["score"]
        if got != want:
            failures.append(f"{name}: computed {got}, fixture expects {want}")
        cmp_ = fx["expect"].get("comparative")
        if cmp_ and "baseline_score" in cmp_:
            g2 = json.loads(json.dumps(fx["given"]))
            if "rental_registration_match=false" in cmp_["baseline"]:
                g2["rental_registration_match"] = False
            if "vision={}" in cmp_["baseline"]:
                g2["vision"] = {}
            # A structured override rather than one more phrase sniffed out of
            # the prose: a comparative that changes a parcel field names it.
            g2["parcel"].update(cmp_.get("baseline_parcel") or {})
            got_b = score(g2)
            if got_b != cmp_["baseline_score"]:
                failures.append(f"{name}: baseline computed {got_b}, fixture expects {cmp_['baseline_score']}")
    n = len(list(GOLDEN.glob("*.json")))
    if failures:
        print(f"FAIL — {len(failures)} mismatch(es) across {n} fixtures:")
        for x in failures:
            print("  ", x)
        sys.exit(1)
    print(f"OK — all {n} fixtures reproduce from the R6 rules (incl. comparatives).")

if __name__ == "__main__":
    main()
