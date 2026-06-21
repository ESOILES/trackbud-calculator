"""
TRACK BUD calculator API v2 for the Retell voice agent.
Exact lookups + portfolio analytics + seasonal on-track checks.
Never invents data; says "I don't have that" when a query isn't supported.
"""
import json, os, re
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)
HERE = os.path.dirname(__file__)
with open(os.path.join(HERE, "trackbud_data.json")) as f:
    D = json.load(f)

TODAY = datetime.fromisoformat(D["today"])
YEAR = D["year"]
RECORDS = D["records"]
STATE_NAMES = D["state_names"]
SEASON = D["season"]
SC = D["sealcoat_cutoff"]

CATS = {
    "asphalt": "100% R/R Asphalt Lot", "r and r": "100% R/R Asphalt Lot", "r/r": "100% R/R Asphalt Lot",
    "remove and replace": "100% R/R Asphalt Lot",
    "concrete": "Less Than 100% Concrete",
    "ada": "All ADA Modifications",
    "ac repair": "AC Repairs", "ac repairs": "AC Repairs", "asphalt repair": "AC Repairs", "a c repair": "AC Repairs",
    "sandblast": "Sandblast & Stripe",
    "seal coat": "Seal Coat & Stripe", "sealcoat": "Seal Coat & Stripe",
    "stripe only": "Stripe Only", "stripe": "Stripe Only", "striping": "Stripe Only",
    "weatherproof": "Weatherproofing", "weatherproofing": "Weatherproofing",
}
MONTHS = {m: i for i, m in enumerate(
    ["january","february","march","april","may","june","july","august",
     "september","october","november","december"], 1)}

_ONES = {"zero":0,"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,
         "nine":9,"ten":10,"eleven":11,"twelve":12,"thirteen":13,"fourteen":14,"fifteen":15,
         "sixteen":16,"seventeen":17,"eighteen":18,"nineteen":19}
_TENS = {"twenty":20,"thirty":30,"forty":40,"fifty":50,"sixty":60,"seventy":70,"eighty":80,"ninety":90}


def money(x):
    return "${:,.2f}".format(x)


def spoken_to_number(text):
    t = text.lower().replace("-", " ")
    toks = [w for w in t.split() if w in _ONES or w in _TENS or w in ("hundred", "oh", "o")]
    if not toks:
        return None
    nums, i = [], 0
    while i < len(toks):
        w = toks[i]
        if w in ("oh", "o"):
            nums.append(0); i += 1
        elif w == "hundred":
            if nums: nums[-1] *= 100
            i += 1
        elif w in _TENS:
            val = _TENS[w]
            if i + 1 < len(toks) and toks[i+1] in _ONES and _ONES[toks[i+1]] < 10:
                val += _ONES[toks[i+1]]; i += 1
            nums.append(val); i += 1
        elif w in _ONES:
            nums.append(_ONES[w]); i += 1
        else:
            i += 1
    if not nums:
        return None
    result = 0
    for n in nums:
        if result == 0: result = n
        elif n >= 100: result += n
        elif result < 10 and n >= 10: result = result * 100 + n
        elif result % 100 == 0: result += n
        else: result = result * 10 + n if n < 10 else result + n
    return result


def parse_date(s):
    return datetime.fromisoformat(s) if s else None


def date_status(dt):
    if not dt:
        return "waiting to be scheduled"
    return "completed" if dt < TODAY else "scheduled, upcoming"


def match_category(t):
    for key in sorted(CATS, key=len, reverse=True):
        if key in t:
            return CATS[key]
    return None


def match_state(t):
    for ab, name in STATE_NAMES.items():
        if name.lower() in t:
            return ab
    return None


def store_records(num):
    return [r for r in RECORDS if r["store"] == str(num)]


def store_lookup(num):
    recs = store_records(num)
    if not recs:
        return "I don't have a record for store {} in the {} list.".format(num, YEAR)
    st = recs[0]["state"]
    parts = ["Store {}{}.".format(num, " in " + STATE_NAMES.get(st, st) if st else "")]
    nonstripe = [r for r in recs if r["category"] != "Stripe Only"]
    for r in nonstripe:
        b = money(r["bid"]) if r["bid"] is not None else "no bid"
        parts.append("{}, {}, {}.".format(r["category"], b, date_status(parse_date(r["date"]))))
    # stripe handled by precomputed status (omit when covered by seal coat/sandblast or a center)
    ss = D.get("stripe_status", {}).get(str(num))
    if ss == "striped":
        sr = next((r for r in recs if r["category"] == "Stripe Only" and r["date"]), None)
        parts.append("Stripe only, {}.".format(date_status(parse_date(sr["date"])) if sr else "striped"))
    elif ss == "owed":
        parts.append("Stripe only, still owed this year.")
    elif ss == "review_acr":
        parts.append("Stripe status needs your review: AC repair with no stripe date.")
    elif ss == "review_dup":
        parts.append("Stripe status needs your review: appears more than once in the stripe list.")
    # covered / center: stripe omitted on purpose
    return " ".join(parts)


def cat_filter(cat=None, state=None):
    out = RECORDS
    if cat:
        out = [r for r in out if r["category"] == cat]
    if state:
        out = [r for r in out if r["state"] == state]
    return out


def total_and_avg(recs, cat=None, include_zero_slots=False):
    total = sum(r["bid"] for r in recs if r["bid"] is not None)
    stores = set(r["store"] for r in recs if r["bid"] is not None)
    n = len(stores)
    # seal coat zero-slots: only when explicitly nationwide
    if cat == "Seal Coat & Stripe" and include_zero_slots:
        total += D["seal_zero"]["total"]
        n += D["seal_zero"]["count"]
    avg = total / n if n else 0
    return round(total, 2), n, round(avg, 2)


@app.route("/ask", methods=["POST"])
def ask():
    body = request.get_json(force=True, silent=True) or {}
    args = body.get("args", body)
    q = str(args.get("question", "")).strip()
    store = args.get("store")
    ql = q.lower()

    if store is not None and str(store).strip():
        digits = re.sub(r"[^0-9]", "", str(store))
        if digits:
            return jsonify({"answer": store_lookup(int(digits))})

    if "stripe" in ql and ("how many" in ql or "left" in ql or "forecast" in ql or "owed" in ql):
        ss = D.get("stripe_status", {})
        owed = sorted([s for s, v in ss.items() if v == "owed"], key=int)
        striped = [s for s, v in ss.items() if v == "striped"]
        review = sorted([s for s, v in ss.items() if v.startswith("review")], key=int)
        return jsonify({"answer": "{} stores still owed a stripe in {}. {} already striped. {} need your review and are not counted automatically: stores {}.".format(
            len(owed), YEAR, len(striped), len(review), ", ".join(review))})

    if "on track" in ql or "on schedule" in ql or "are we behind" in ql or "behind schedule" in ql:
        return jsonify({"answer": on_track(ql)})

    if "scheduled" in ql and ("this month" in ql or "next month" in ql or any(m in ql for m in MONTHS)):
        return jsonify({"answer": scheduled_month(ql)})

    if ("unscheduled" in ql or "backlog" in ql or "not scheduled" in ql or "left to schedule" in ql):
        return jsonify({"answer": backlog(ql)})

    if ("completed" in ql or "finished" in ql) and ("upcoming" in ql or "vs" in ql or "versus" in ql or "left" in ql):
        return jsonify({"answer": completed_vs_upcoming(ql)})

    if ("most expensive" in ql or "biggest" in ql or "largest" in ql or "priciest" in ql
            or "least expensive" in ql or "smallest" in ql or "cheapest" in ql):
        return jsonify({"answer": extreme_job(ql)})

    if ("which state" in ql or "what state" in ql or "rank" in ql) and match_category(ql):
        return jsonify({"answer": state_ranking(ql)})

    if "unassigned" in ql or "budget slot" in ql or ("seal coat" in ql and ("available" in ql or "slot" in ql)):
        z = D["seal_zero"]
        return jsonify({"answer": "You have {} unassigned seal coat budget slots worth {} total.".format(z["count"], money(z["total"]))})

    m = re.search(r"\b(\d{1,3})\b", ql)
    store_num = int(m.group(1)) if m else spoken_to_number(ql)
    cat = match_category(ql)
    state = match_state(ql)

    if state and cat:
        recs = cat_filter(cat, state)
        if not recs:
            return jsonify({"answer": "I don't have any {} in {} for {}.".format(cat, STATE_NAMES.get(state, state), YEAR)})
        total, n, avg = total_and_avg(recs, cat, include_zero_slots=False)
        name = STATE_NAMES.get(state, state)
        if "average" in ql or "averaging" in ql or "per store" in ql:
            return jsonify({"answer": "Average {} per store in {} is {}, across {} stores.".format(cat, name, money(avg), n)})
        return jsonify({"answer": "Total {} in {} is {}, across {} stores.".format(cat, name, money(total), n)})

    if cat and store_num is None:
        recs = cat_filter(cat)
        if not recs:
            return jsonify({"answer": "I don't have {} data for {}.".format(cat, YEAR)})
        total, n, avg = total_and_avg(recs, cat, include_zero_slots=True)
        if "average" in ql or "averaging" in ql or "per store" in ql:
            return jsonify({"answer": "Average {} per store nationwide is {}, across {} stores.".format(cat, money(avg), n)})
        return jsonify({"answer": "Total {} nationwide is {}, across {} stores.".format(cat, money(total), n)})

    if store_num is not None:
        return jsonify({"answer": store_lookup(store_num)})

    if "grand total" in ql or "total work" in ql or ("total" in ql and "everything" in ql):
        gt = sum(r["bid"] for r in RECORDS if r["bid"] is not None) + D["seal_zero"]["total"]
        return jsonify({"answer": "Grand total of all pavement work nationwide is {} for {}.".format(money(round(gt, 2)), YEAR)})

    return jsonify({"answer": "I don't have that. Ask about a store, a category total or average, a state, the stripe forecast, what's scheduled this month, the unscheduled backlog, the biggest job, or whether we're on track."})


def extreme_job(ql):
    cat = match_category(ql)
    state = match_state(ql)
    recs = [r for r in cat_filter(cat, state) if r["bid"] is not None]
    if not recs:
        return "I don't have matching jobs for that."
    least = "least" in ql or "smallest" in ql or "cheapest" in ql
    job = min(recs, key=lambda r: r["bid"]) if least else max(recs, key=lambda r: r["bid"])
    where = STATE_NAMES.get(job["state"], job["state"])
    kind = "least expensive" if least else "most expensive"
    scope = ""
    if cat: scope += " " + cat
    if state: scope += " in " + STATE_NAMES.get(state, state)
    return "The {}{} job is {} at store {} in {} — {}.".format(
        kind, scope, money(job["bid"]), job["store"], where, job["category"])


def state_ranking(ql):
    cat = match_category(ql)
    if not cat:
        return "Tell me which category to rank states by."
    by = {}
    for r in cat_filter(cat):
        if r["bid"] is None: continue
        by[r["state"]] = by.get(r["state"], 0.0) + r["bid"]
    if not by:
        return "I don't have {} data to rank.".format(cat)
    ranked = sorted(by.items(), key=lambda kv: -kv[1])
    top = ranked[0]
    parts = ["{} has the most {} at {}.".format(STATE_NAMES.get(top[0], top[0]), cat, money(round(top[1], 2)))]
    if len(ranked) > 1:
        rest = ", ".join("{} {}".format(STATE_NAMES.get(s, s), money(round(v, 2))) for s, v in ranked[1:4])
        parts.append("Next: " + rest + ".")
    return " ".join(parts)


def backlog(ql):
    cat = match_category(ql)
    state = match_state(ql)
    if cat:
        recs = cat_filter(cat, state)
        uns = [r for r in recs if not r["date"]]
        amt = sum(r["bid"] for r in uns if r["bid"] is not None)
        scope = cat + (" in " + STATE_NAMES.get(state, state) if state else "")
        return "{} has {} jobs still unscheduled, worth {}.".format(scope, len(uns), money(round(amt, 2)))
    by = {}
    for r in RECORDS:
        if r["date"]: continue
        e = by.setdefault(r["category"], [0, 0.0])
        e[0] += 1
        if r["bid"] is not None: e[1] += r["bid"]
    if not by:
        return "Nothing is unscheduled."
    parts = ["Unscheduled backlog by category:"]
    for c, (cnt, amt) in sorted(by.items(), key=lambda kv: -kv[1][1]):
        parts.append("{}: {} jobs, {}.".format(c, cnt, money(round(amt, 2))))
    return " ".join(parts)


def completed_vs_upcoming(ql):
    cat = match_category(ql)
    state = match_state(ql)
    recs = cat_filter(cat, state)
    done = sum(1 for r in recs if r["date"] and parse_date(r["date"]) < TODAY)
    up = sum(1 for r in recs if r["date"] and parse_date(r["date"]) >= TODAY)
    uns = sum(1 for r in recs if not r["date"])
    scope = (cat + " " if cat else "") + ("in " + STATE_NAMES.get(state, state) + " " if state else "")
    return "{}status: {} completed, {} scheduled upcoming, {} not scheduled yet.".format(scope, done, up, uns)


def scheduled_month(ql):
    if "next month" in ql:
        target = TODAY.month + 1
    elif "this month" in ql:
        target = TODAY.month
    else:
        target = next((MONTHS[m] for m in MONTHS if m in ql), TODAY.month)
    cat = match_category(ql)
    state = match_state(ql)
    recs = cat_filter(cat, state)
    hits = [r for r in recs if r["date"] and parse_date(r["date"]).month == target and parse_date(r["date"]).year == YEAR]
    mname = [k for k, v in MONTHS.items() if v == target][0].capitalize()
    if not hits:
        return "Nothing is scheduled in {} {} for that.".format(mname, YEAR)
    amt = sum(r["bid"] for r in hits if r["bid"] is not None)
    return "{} jobs scheduled in {} {}, worth {}.".format(len(hits), mname, YEAR, money(round(amt, 2)))


def sealcoat_cutoff_month(store):
    st = D["store_state"].get(store, "")
    if st in SC["hot_states"]: return 10
    if st in SC["cold_states"]: return 8
    if st == "CA":
        return 10 if D["ca_region"].get(store) == "S" else 8
    return 9


def on_track(ql):
    out = []
    cur = TODAY.month
    for cat, cfg in SEASON.items():
        recs = [r for r in RECORDS if r["category"] == cat]
        uns = [r for r in recs if not r["date"]]
        total = len(recs)
        tm = cfg["target_month"]
        if cur > tm and uns:
            out.append("{}: BEHIND — target was {}, {} of {} still unscheduled.".format(cat, cfg["label"], len(uns), total))
        elif uns:
            out.append("{}: {} of {} still to schedule before {}.".format(cat, len(uns), total, cfg["label"]))
        else:
            out.append("{}: on track, all {} scheduled or done.".format(cat, total))
    sc_recs = [r for r in RECORDS if r["category"] == "Seal Coat & Stripe"]
    sc_uns = [r for r in sc_recs if not r["date"]]
    behind = [r for r in sc_uns if cur > sealcoat_cutoff_month(r["store"])]
    if behind:
        out.append("Seal Coat: {} unscheduled stores are past their season cutoff — at risk.".format(len(behind)))
    else:
        out.append("Seal Coat: {} stores still to schedule this season.".format(len(sc_uns)))
    return " ".join(out)


@app.route("/", methods=["GET"])
def health():
    return "TRACK BUD calculator v2 running. {} job records loaded.".format(len(RECORDS))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
