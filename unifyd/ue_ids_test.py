"""Offline test for the UberEats id codec: python3 unifyd/ue_ids_test.py

These are REAL values from the live warehouse — sitemap tokens on the left, the canonical UUIDs the
catalog and BFF report on the right. Getting this wrong is what made 208,624 observed stores look
like 15.

The load-bearing assertions:
  • the codec round-trips, both directions, on real data
  • the SQL form and the Python form agree — the join runs in DuckDB, so a divergence between them
    would silently produce a different answer than the tests check
  • a value that is NOT a token is not mangled into a fake UUID (and vice versa)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ue_ids

passed = failed = 0


def ok(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print("  ok   %s" % name)
    else:
        failed += 1
        print("  FAIL %s" % name)


def eq(name, got, want):
    ok("%s (got %r)" % (name, got), got == want)


# Real pairs, decoded from live ubereats_sitemap rows.
PAIRS = [
    ("3GYoBDgAU6-me98dDz_kSw", "dc662804-3800-53af-a67b-df1d0f3fe44b"),
    ("Z9z-7q2cUyqsggTGwFmaFQ", "67dcfeee-ad9c-532a-ac82-04c6c0599a15"),
    ("hm-ihVRaUp-jgVglFGdlFw", "866fa285-545a-529f-a381-582514676517"),
    ("Qc98lmD8TDCAFryW0GMsoQ", "41cf7c96-60fc-4c30-8016-bc96d0632ca1"),
    ("QIF8p0r5SH6CiuHC3zYplA", "40817ca7-4af9-487e-828a-e1c2df362994"),
    ("04ky9hfzRImUMfHeyrHqLw", "d38932f6-17f3-4489-9431-f1decab1ea2f"),
    ("vSEa4G6HWU6EVs92wJ2i-A", "bd211ae0-6e87-594e-8456-cf76c09da2f8"),
]


def test_decode_real_values():
    for tok, uid in PAIRS:
        eq("decode %s" % tok, ue_ids.token_to_uuid(tok), uid)


def test_round_trip():
    for tok, uid in PAIRS:
        eq("encode back %s" % uid[:8], ue_ids.uuid_to_token(uid), tok)
        eq("round trip %s" % tok[:8], ue_ids.token_to_uuid(ue_ids.uuid_to_token(uid)), uid)


def test_canonical_accepts_either_spelling():
    for tok, uid in PAIRS[:3]:
        eq("canonical(token)", ue_ids.canonical(tok), uid)
        eq("canonical(uuid)", ue_ids.canonical(uid), uid)


# Real store names that are EXACTLY 22 characters — the length of a base64url token. A live run
# died on the first of these: a length-only guard let it into FROM_BASE64, which raised
# "Could not decode string ... invalid byte value '32'" and killed the build.
LOOKALIKES_22 = ["Blackstone and Bullard", "Kroger On The Rhine!!!", "Store #12345 Downtown!"]


def test_22_char_non_tokens_are_rejected():
    for s in LOOKALIKES_22:
        eq("len is exactly 22 (guard is not vacuous)", len(s), 22)
        eq("token_to_uuid(%r) is None" % s, ue_ids.token_to_uuid(s), None)
        eq("canonical(%r) is None" % s, ue_ids.canonical(s), None)

    import duckdb
    con = duckdb.connect()
    for s in LOOKALIKES_22:
        got = con.execute("SELECT %s" % ue_ids.sql_canonical("'%s'" % s.replace("'", "''"))).fetchone()[0]
        eq("SQL canonical(%r) is NULL, not an error" % s, got, None)
    # And in bulk, the shape the real join uses — this is what actually crashed.
    con.execute("CREATE TABLE t AS SELECT * FROM (VALUES ('Blackstone and Bullard'),"
                "('3GYoBDgAU6-me98dDz_kSw'),('Kroger On The Rhine!!!'),('1748913')) v(sid)")
    rows = con.execute("SELECT sid, %s AS uid FROM t ORDER BY sid" % ue_ids.sql_canonical("sid")).fetchall()
    got = {r[0]: r[1] for r in rows}
    eq("the real token still decodes in bulk", got["3GYoBDgAU6-me98dDz_kSw"],
       "dc662804-3800-53af-a67b-df1d0f3fe44b")
    eq("the 22-char name yields NULL in bulk", got["Blackstone and Bullard"], None)
    eq("a short non-token yields NULL in bulk", got["1748913"], None)


def test_non_ids_are_not_mangled():
    for junk in ("", None, "1748913", "Birmingham 280 Corridor", "Online", "abc",
                 "not-a-uuid-at-all", "0123456789012345678901234"):
        eq("token_to_uuid(%r) is None" % junk, ue_ids.token_to_uuid(junk), None)
    for junk in ("", None, "3GYoBDgAU6-me98dDz_kSw", "1001", "hello"):
        eq("uuid_to_token(%r) is None" % junk, ue_ids.uuid_to_token(junk), None)
    eq("canonical of a plain id stays None", ue_ids.canonical("1748913"), None)


def test_sql_matches_python():
    """The join runs in DuckDB. If the SQL form disagreed with the Python form, the tests would pass
    while production produced a different answer."""
    import duckdb
    con = duckdb.connect()
    for tok, uid in PAIRS:
        got = con.execute("SELECT %s" % ue_ids.sql_token_to_uuid("'%s'" % tok)).fetchone()[0]
        eq("SQL decode %s" % tok[:8], got, uid)
        got2 = con.execute("SELECT %s" % ue_ids.sql_canonical("'%s'" % tok)).fetchone()[0]
        eq("SQL canonical(token) %s" % tok[:8], got2, uid)
        got3 = con.execute("SELECT %s" % ue_ids.sql_canonical("'%s'" % uid)).fetchone()[0]
        eq("SQL canonical(uuid) %s" % uid[:8], got3, uid)

    # A non-token must yield NULL in SQL, matching Python's None.
    for junk in ("1748913", "Online", "Birmingham 280 Corridor"):
        got = con.execute("SELECT %s" % ue_ids.sql_canonical("'%s'" % junk)).fetchone()[0]
        eq("SQL canonical(%r) is NULL" % junk, got, None)


def test_version_nibble_is_the_tell():
    """The diagnostic that cracked this: a canonical UUID always has 4 or 5 at position 15; the
    base64 token does not."""
    for tok, uid in PAIRS:
        ok("decoded %s has a real version nibble" % tok[:8], uid[14] in "45")
    nibbles = {tok[14] for tok, _ in PAIRS}
    ok("tokens do NOT share that property (they look random)", len(nibbles - set("45")) > 0)


if __name__ == "__main__":
    for fn in (test_decode_real_values, test_round_trip, test_canonical_accepts_either_spelling,
               test_22_char_non_tokens_are_rejected,
               test_non_ids_are_not_mangled, test_sql_matches_python,
               test_version_nibble_is_the_tell):
        print(fn.__name__)
        fn()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
