"""
TRACK BUD calculator API for the Retell voice agent.
Exact lookups — never fuzzy, never invents data.
Retell calls one endpoint, /ask, with a function call.
"""
import json, os, re
from flask import Flask, request, jsonify

app = Flask(__name__)

with open(os.path.join(os.path.dirname(__file__), "trackbud_data.json")) as f:
    DATA = json.load(f)

CATS = {
    "asphalt": "100% R/R Asphalt Lot",
    "r and r": "100% R/R Asphalt Lot",
    "r/r": "100% R/R Asphalt Lot",
    "concrete": "Less Than 100% Concrete",
    "ada": "All ADA Modifications",
    "ac repair": "AC Repairs",
    "ac repairs": "AC Repairs",
    "asphalt repair": "AC Repairs",
    "sandblast": "Sandblast & Stripe",
    "seal coat": "Seal Coat & Stripe",
    "sealcoat": "Seal Coat & Stripe",
    "stripe only": "Stripe Only",
    "stripe": "Stripe Only",
    "weatherproof": "Weatherproofing",
    "weatherproofing": "Weatherproofing",
}
STATE_ABBR = {v["name"].lower(): k for k, v in DATA["by_state"].items()}


def money(x):
    return "${:,.2f}".format(x)


_ONES = {"zero":0,"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,
         "eight":8,"nine":9,"ten":10,"eleven":11,"twelve":12,"thirteen":13,
         "fourteen":14,"fifteen":15,"sixteen":16,"seventeen":17,"eighteen":18,"nineteen":19}
_TENS = {"twenty":20,"thirty":30,"forty":40,"fifty":50,"sixty":60,"seventy":70,
         "eighty":80,"ninety":90}


def spoken_to_number(text):
    """Convert spoken store numbers to an int.
    Handles: 'two fifty eight'->258, 'two hundred fifty eight'->258,
    'eighty four'->84, 'three oh five'->305, 'one'->1."""
    t = text.lower().replace("-", " ")
    toks = [w for w in t.split() if w in _ONES or w in _TENS or w == "hundred" or w == "oh" or w == "o"]
    if not toks:
        return None
    # 'oh'/'o' acts as zero in digit-strings like 'three oh five'
    # Strategy: build number left to right.
    # If a token is hundreds-scale or a leading 1-9 followed by tens, treat as concatenation.
    nums = []
    i = 0
    while i < len(toks):
        w = toks[i]
        if w in ("oh", "o"):
            nums.append(0); i += 1
        elif w == "hundred":
            if nums:
                nums[-1] *= 100
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
    # Combine: 'two' '58' -> 2*100+58=258 ; 'two hundred' '58' -> 200+58=258
    result = 0
    for n in nums:
        if result == 0:
            result = n
        elif n >= 100:
            result += n
        elif result < 10 and n >= 10:
            result = result * 100 + n   # 'two' + 'fifty eight' -> 258
        elif result % 100 == 0:
            result += n                  # 'two hundred' + 'fifty eight' -> 258
        else:
            result = result * 10 + n if n < 10 else result + n
    return result


def match_category(text):
    t = text.lower()
    # longest key first so "ac repairs" wins over "ac"
    for key in sorted(CATS, key=len, reverse=True):
        if key in t:
            return CATS[key]
    return None


def match_state(text):
    t = text.lower()
    for name, ab in STATE_ABBR.items():
        if name in t:
            return ab
    return None


def store_lookup(num):
    s = DATA["stores"].get(str(num))
    if not s:
        return "I don't have a record for store {} in the 2026 list.".format(num)
    parts = ["Store {}{}.".format(num, " in " + s["state_name"] if s["state_name"] else "")]
    for p in s["projects"]:
        bid = money(p["bid"]) if p["bid"] is not None else "no bid"
        parts.append("{}, {}, {}.".format(p["project"], bid, p["status"]))
    st = s.get("stripe")
    if st:
        if st["state"] == "striped":
            parts.append("Stripe only, striped {}.".format(st.get("date") or "date on file"))
        elif st["state"] == "owed":
            parts.append("Stripe only, still owed this year.")
        elif st["state"] == "review":
            parts.append("Stripe status needs your review: {}.".format(st["reason"]))
        # covered / center: stripe omitted on purpose
    return " ".join(parts)


@app.route("/ask", methods=["POST"])
def ask():
    body = request.get_json(force=True, silent=True) or {}
    # Retell sends args under "args" for custom functions
    args = body.get("args", body)
    q = str(args.get("question", "")).strip()
    store = args.get("store")

    # explicit store arg wins
    if store is not None and str(store).strip():
        digits = re.sub(r"[^0-9]", "", str(store))
        if digits:
            return jsonify({"answer": store_lookup(int(digits))})

    ql = q.lower()

    # seal coat unassigned budget — check BEFORE category matching
    if "unassigned" in ql or "budget slot" in ql or ("seal coat" in ql and ("left" in ql or "available" in ql or "slot" in ql)):
        z = DATA["seal_zero"]
        return jsonify({"answer": "You have {} unassigned seal coat budget slots worth {} total.".format(z["count"], money(z["total"]))})

    # stripe forecast
    if "stripe" in ql and ("how many" in ql or "left" in ql or "forecast" in ql or "owed" in ql):
        f = DATA["stripe_forecast"]
        ans = ("{} stores still owed a stripe in {}. {} already striped this year. "
               "{} stores need your review and are not counted automatically: stores {}."
               ).format(f["owed_count"], DATA["year"], f["striped_count"],
                        len(f["review"]), ", ".join(f["review"]))
        return jsonify({"answer": ans})

    # store number — digits first, then spoken words
    m = re.search(r"\b(\d{1,3})\b", ql)
    store_num = int(m.group(1)) if m else spoken_to_number(ql)
    cat = match_category(ql)
    state = match_state(ql)

    # state + category totals/averages
    if state and cat:
        c = DATA["by_state"].get(state, {}).get("categories", {}).get(cat)
        name = DATA["by_state"][state]["name"]
        if not c:
            return jsonify({"answer": "I don't have any {} in {} for {}.".format(cat, name, DATA["year"])})
        if "average" in ql or "averaging" in ql or "per store" in ql:
            return jsonify({"answer": "Average {} per store in {} is {}, across {} stores.".format(cat, name, money(c["avg"]), c["stores"])})
        return jsonify({"answer": "Total {} in {} is {}, across {} stores.".format(cat, name, money(c["total"]), c["stores"])})

    # nationwide category
    if cat and store_num is None:
        c = DATA["nationwide"].get(cat)
        if not c:
            return jsonify({"answer": "I don't have {} data for {}.".format(cat, DATA["year"])})
        if "average" in ql or "averaging" in ql or "per store" in ql:
            return jsonify({"answer": "Average {} per store nationwide is {}, across {} stores.".format(cat, money(c["avg"]), c["stores"])})
        return jsonify({"answer": "Total {} nationwide is {}, across {} stores.".format(cat, money(c["total"]), c["stores"])})

    # store lookup
    if store_num is not None:
        return jsonify({"answer": store_lookup(store_num)})

    # grand total
    if "grand total" in ql or ("total" in ql and "everything" in ql) or "total work" in ql:
        return jsonify({"answer": "Grand total of all pavement work nationwide is {} for {}.".format(money(DATA["grand_total"]), DATA["year"])})

    # seal coat unassigned budget
    if "unassigned" in ql or "budget slot" in ql or ("seal coat" in ql and ("left" in ql or "available" in ql)):
        z = DATA["seal_zero"]
        return jsonify({"answer": "You have {} unassigned seal coat budget slots worth {} total.".format(z["count"], money(z["total"]))})

    return jsonify({"answer": "I don't have that. Try asking about a specific store number, a category total, a state average, or the stripe forecast."})


@app.route("/", methods=["GET"])
def health():
    return "TRACK BUD calculator running. {} stores loaded.".format(len(DATA["stores"]))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
