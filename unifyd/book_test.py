"""Tests for the book's measure aggregations: python book_test.py
Builds a 24-month synthetic warehouse so YoY growth has a prior period, then
checks every measure in domain.MEASURES computes something real — not the old
silent fallback to revenue. No network."""
import os, shutil
import seed, book, warehouse

passed = 0
def ok(name, cond):
    global passed
    print(("ok   " if cond else "FAIL ") + name)
    passed += 1 if cond else 0

os.environ.pop("AWS_ENDPOINT_URL_S3", None); os.environ.pop("BUCKET_NAME", None)
shutil.rmtree(warehouse._LOCAL_DIR, ignore_errors=True)
seed.build(n_products=30, n_accounts=40, months=24)

def vals(measure, dim="category"):
    return {r["name"]: r["value"] for r in book.cuts(dim, measure)}

rev = vals("revenue")
ok("revenue is dollars", all(v > 1000 for v in rev.values()))

# THE BUG: growth used to return revenue verbatim. It must not anymore.
grow = vals("rev_growth")
ok("rev_growth differs from revenue", grow and any(rev.get(k) != grow.get(k) for k in grow))
ok("rev_growth is a sane percentage", grow and all(-100 <= v <= 1000 for v in grow.values()))
ok("yoy non-empty with 24 months", len(book.cuts("brand", "rev_growth")) > 0)

share = book.cuts("category", "share")
ok("share sums to ~100%", abs(sum(r["value"] for r in share) - 100) < 0.5)
ok("share values are percentages", all(0 <= r["value"] <= 100 for r in share))

prem = list(vals("premium").values())
ok("premium is an index centered near 100", prem and any(v > 100 for v in prem) and any(v < 100 for v in prem) and all(10 <= v <= 400 for v in prem))

vel = list(vals("velocity").values())
ok("velocity is positive per-outlet rate", vel and all(v > 0 for v in vel))

pod = list(vals("pod").values())
ok("pod are whole-number account counts", pod and all(float(v).is_integer() for v in pod))

TOTAL = 9
print("\n%d/%d passed" % (passed, TOTAL))
raise SystemExit(0 if passed == TOTAL else 1)
