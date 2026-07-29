#!/usr/bin/env python3
"""zcta_test.py — the no-silent-default guard. stdlib only, no warehouse, no network.

This exists because of a live report: a search for "Houston, TX" returned a map of Tampa. The cause
was one line — `re.sub(r"[^0-9]", "", zip) or "33602"` — which turned every non-numeric location into
a hardcoded Florida ZIP and then answered with total confidence.

So the load-bearing test is not that Houston resolves; it is that **nothing resolves to a place the
user did not ask for**. Every unresolvable input must come back as an error, and no input may ever
produce 33602 unless 33602 was typed.

    python3 unifyd/zcta_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zcta                                                   # noqa: E402

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


def main():
    print("zcta.resolve")

    # --- the regression itself -------------------------------------------------------------------
    # Every one of these used to become 33602 (Tampa) and render as a successful search.
    for bad in ("Houston, TX", "Houston", "", "   ", "somewhere", "TX", "Austin, Texas!!"):
        r = zcta.resolve(bad)
        got_default = r.get("zip") == "33602"
        check("%r never silently becomes Tampa" % bad, not got_default, r)

    for bad in ("", "   ", "Houston", "TX"):
        r = zcta.resolve(bad)
        check("%r errors rather than answering" % bad, "error" in r and not r.get("zip"), r)

    # --- ZIP parsing ------------------------------------------------------------------------------
    r = zcta.resolve("77002")
    check("bare ZIP resolves", r.get("zip") == "77002" and r.get("how") == "zip")

    r = zcta.resolve("Houston TX 77002")
    check("ZIP inside a string wins", r.get("zip") == "77002")

    r = zcta.resolve("33602-1234")
    check("ZIP+4 keeps the 5-digit", r.get("zip") == "33602")

    r = zcta.resolve("  77002  ")
    check("whitespace tolerated", r.get("zip") == "77002")

    # A typed Tampa ZIP must of course still work — the guard is against SUBSTITUTION, not against
    # Tampa. Asserting only "never 33602" would pass a module that rejected the ZIP outright.
    r = zcta.resolve("33602")
    check("an explicitly typed 33602 still resolves",
          r.get("zip") == "33602" and r.get("how") == "zip")

    # --- state requirement ------------------------------------------------------------------------
    r = zcta.resolve("Houston")
    check("bare city asks for a state", "state" in (r.get("error") or ""))
    check("bare city error names the city back", "Houston" in (r.get("error") or ""), r)

    # --- nearest() is honest when unbuilt ---------------------------------------------------------
    check("nearest returns None without a reference table", zcta.nearest(29.76, -95.36) is None)
    check("nearest tolerates a missing point", zcta.nearest(None, None) is None)

    # --- the city path names the RIGHT failure ----------------------------------------------------
    # Locally the reference isn't built; the error must say that, not blame the user's city.
    r = zcta.resolve("Houston, TX")
    err = r.get("error") or ""
    check("city path errors", bool(err) and not r.get("zip"))
    check("unbuilt reference is diagnosed as such, not as a bad city",
          ("not built" in err) or ("couldn't place" in err), err)

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
