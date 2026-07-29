#!/usr/bin/env python3
"""serve_advisor_test.py — the guard, not the model. stdlib only, no API key, no network.

What is worth testing here is NOT whether Claude writes a good sentence — it is whether a bad or
adversarial model response can ever put a venue in front of a user that the warehouse did not
supply. `verify()` is that boundary, so it is tested directly with the payloads a model actually
produces when it is wrong: an out-of-range index, a repeat, a string where an int belongs, an empty
rationale, prose naming a bar we never sent.

    python3 unifyd/serve_advisor_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serve_advisor as A                                     # noqa: E402

FAILED = []
RAN = []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


SERVES = [
    {"venue": "The Wildflower", "drink": "Old Fashioned", "price": 10.0, "city": "Sanford",
     "state": "FL", "distance_mi": 3.2, "description": "bourbon, demerara, ango & orange bitters"},
    {"venue": "Outpost Burgers", "drink": "Million Dollar Old Fashion", "price": 34.0,
     "city": "Orlando", "state": "FL", "distance_mi": None,
     "description": "rye, black walnut bitters, luxardo cherry"},
    {"venue": "Hidden Bar", "drink": "Peach Old Fashioned", "price": None, "city": "Tampa",
     "state": "FL", "distance_mi": 41.0, "description": ""},
]


def main():
    print("serve_advisor")

    # --- render: only provable fields reach the model -------------------------------------------
    txt = A.render(SERVES)
    check("render numbers every serve", all(("#%d" % i) in txt for i in range(3)))
    check("render carries the build", "black walnut bitters" in txt)
    check("render names a missing build", "(no build listed on the menu)" in txt)
    check("render states missing price", "price not listed" in txt)
    check("render omits distance when unknown", txt.count(" mi") == 2, txt)

    # --- verify: the guard ----------------------------------------------------------------------
    good = {"picks": [{"index": 1, "angle": "rye + black walnut", "why": "Swaps bourbon for rye."}],
            "classic": "Whiskey, sugar, bitters, orange."}
    r = A.verify(good, SERVES)
    check("a valid pick resolves", len(r["picks"]) == 1 and r["dropped"] == 0)
    check("pick carries the WAREHOUSE row, not model text",
          r["picks"][0]["venue"] == "Outpost Burgers" and r["picks"][0]["price"] == 34.0)
    check("pick carries the model's why", r["picks"][0]["why"].startswith("Swaps"))
    check("classic passes through", r["classic"].startswith("Whiskey"))

    # An index past the end is the shape a hallucinated venue takes once the model can only speak
    # in indices — it cannot name a bar, so it invents a number instead. Must be dropped.
    r = A.verify({"picks": [{"index": 9, "angle": "x", "why": "y"}]}, SERVES)
    check("out-of-range index dropped", r["picks"] == [] and r["dropped"] == 1)

    r = A.verify({"picks": [{"index": -1, "angle": "x", "why": "y"}]}, SERVES)
    check("negative index dropped", r["picks"] == [] and r["dropped"] == 1)

    r = A.verify({"picks": [{"index": "1", "angle": "x", "why": "y"}]}, SERVES)
    check("string index dropped (not coerced)", r["picks"] == [] and r["dropped"] == 1)

    r = A.verify({"picks": [{"index": 0, "angle": "a", "why": "w"},
                            {"index": 0, "angle": "b", "why": "w2"}]}, SERVES)
    check("duplicate index dropped once", len(r["picks"]) == 1 and r["dropped"] == 1)

    r = A.verify({"picks": [{"index": 0, "angle": "a", "why": "   "}]}, SERVES)
    check("empty rationale dropped", r["picks"] == [] and r["dropped"] == 1)

    # A model that ignores the schema and writes prose about a venue gets nothing through: there is
    # no path from a free-text field to the rendered venue/drink/price at all.
    r = A.verify({"picks": [{"venue": "Somewhere We Never Sent", "why": "great reviews"}]}, SERVES)
    check("pick without an index dropped", r["picks"] == [] and r["dropped"] == 1)

    r = A.verify({}, SERVES)
    check("empty payload is ok-with-no-picks, not an error",
          r["ok"] and r["picks"] == [] and r["dropped"] == 0)

    mixed = A.verify({"picks": [{"index": 0, "angle": "a", "why": "w"},
                                {"index": 7, "angle": "b", "why": "w"}]}, SERVES)
    check("partial payload keeps the good pick and counts the bad",
          len(mixed["picks"]) == 1 and mixed["dropped"] == 1)

    # --- availability: never silently off -------------------------------------------------------
    old_key, old_off = os.environ.get("ANTHROPIC_API_KEY"), os.environ.get("SERVE_NO_LLM")
    try:
        os.environ["SERVE_NO_LLM"] = "1"
        ok, why = A.available()
        check("disabled reports a reason", not ok and "SERVE_NO_LLM" in why)
        del os.environ["SERVE_NO_LLM"]
        os.environ["ANTHROPIC_API_KEY"] = ""
        ok, why = A.available()
        check("no key reports a reason", not ok and "ANTHROPIC_API_KEY" in why)
        res = A.recommend("old fashioned", SERVES)
        check("recommend without a key returns the reason, not silence",
              res["ok"] is False and "ANTHROPIC_API_KEY" in res["reason"])
        # Emptiness outranks capability: with NO key at all, "no serves" is still the answer, because
        # sending someone to go set an API key when the real problem is an empty result set is a
        # wrong diagnosis dressed as a helpful one.
        res = A.recommend("old fashioned", [])
        check("no serves reported even with no key, and before any API call",
              res["ok"] is False and "no serves" in res["reason"])
    finally:
        for k, v in (("ANTHROPIC_API_KEY", old_key), ("SERVE_NO_LLM", old_off)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
