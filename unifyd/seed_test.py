"""Coherence tests for the synthetic book: python seed_test.py
Verifies referential integrity + that revenue reconciles across every cut (the point of a
star schema), through the real Parquet+DuckDB warehouse path. No network."""
import os, shutil
import seed, domain, warehouse

passed = 0
def ok(name, cond):
    global passed
    print(("ok   " if cond else "FAIL ") + name)
    passed += 1 if cond else 0

os.environ.pop("AWS_ENDPOINT_URL_S3", None); os.environ.pop("BUCKET_NAME", None)
shutil.rmtree(warehouse._LOCAL_DIR, ignore_errors=True)

data = seed.generate(n_products=30, n_accounts=40, months=12)
ok("4 tables generated", set(data) == set(domain.TABLES))
ok("products/accounts/dates sized", len(data["dim_product"]) == 30 and len(data["dim_account"]) == 40 and len(data["dim_date"]) == 12)
ok("facts non-trivial", len(data["fact_depletion"]) > 1000)

# referential integrity in memory
pids = {p["product_id"] for p in data["dim_product"]}
aids = {a["account_id"] for a in data["dim_account"]}
dids = {d["date_id"] for d in data["dim_date"]}
orphans = [f for f in data["fact_depletion"] if f["product_id"] not in pids or f["account_id"] not in aids or f["date_id"] not in dids]
ok("no orphan facts", not orphans)

# revenue == cases * unit_price * 12 (money ties to volume)
price = {p["product_id"]: p["unit_price"] for p in data["dim_product"]}
mismatch = [f for f in data["fact_depletion"] if abs(f["revenue"] - f["cases"] * price[f["product_id"]] * 12) > 0.011]
ok("revenue reconciles to cases*price", not mismatch)

# determinism
ok("deterministic build", seed.generate(n_products=30, n_accounts=40, months=12)["fact_depletion"] == data["fact_depletion"])

# --- through the warehouse (Parquet + DuckDB), reconcile across cuts ---
summary = seed.build(n_products=30, n_accounts=40, months=12)
con = warehouse.connect()
for name in domain.TABLES:
    con.execute("CREATE VIEW %s AS SELECT * FROM read_parquet('%s')" % (name, warehouse.uri(name)))

total = con.execute("SELECT round(sum(revenue),2) FROM fact_depletion").fetchone()[0]
ok("build summary total matches fact total", abs(summary["total_revenue"] - total) < 0.01)

by_cat = con.execute("""SELECT round(sum(f.revenue),2) FROM fact_depletion f
                        JOIN dim_product p USING(product_id)""").fetchone()[0]
ok("category-join total == grand total (no orphans, no double-count)", abs(by_cat - total) < 0.01)

by_chan = con.execute("""SELECT round(sum(f.revenue),2) FROM fact_depletion f
                         JOIN dim_account a USING(account_id)""").fetchone()[0]
ok("account-join total == grand total", abs(by_chan - total) < 0.01)

cats = con.execute("""SELECT p.category, round(sum(f.revenue),2) r FROM fact_depletion f
                      JOIN dim_product p USING(product_id) GROUP BY 1 ORDER BY r DESC""").fetchall()
ok("category breakdown sums to total", abs(sum(c[1] for c in cats) - total) < 0.02)
ok("multiple categories present", len(cats) >= 3)

TOTAL = 11
print("\n%d/%d passed  (book revenue: $%s)" % (passed, TOTAL, format(int(total), ",")))
raise SystemExit(0 if passed == TOTAL else 1)
