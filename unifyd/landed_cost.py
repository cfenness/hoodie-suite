"""landed_cost.py — the tax translation layer: stack federal + state tax onto a base cost.

Derived, never landed source data ([[normalization-scout]]): given a base cost and the item's shape
(class, ABV, size), resolve the applicable rates from tax_rates.py and return an ITEMIZED cost stack.
This is the function ordering.html / keystone pricing calls to turn a naive "MSRP = 2x base" into a
true, per-state effective floor, and that price_signal.py calls to normalize an observed shelf price to
a PRE-TAX basis so cross-state prices are comparable ([[price-clustering-signal]]).

Unit math (US):
  gallons      = size_ml / 3785.411784
  proof_gallon = gallons * (abv/100 * 2)         # proof = 2 x ABV
  barrel       = gallons / 31.0                   # US beer barrel
Federal excise applies on proof_gallon (spirits), wine_gallon (wine/cider), or barrel (beer). State
excise is nearly always quoted per gallon; control states carry no clean statutory excise (the markup IS
the tax) so we FLAG rather than invent a number.

Honest gaps: a rate we can't resolve (unverified seed cell, missing state, control-state excise, unknown
ABV for an ABV-tiered class) is reported in `warnings` and treated as 0 for that line — never silently
absorbed, so a too-cheap landed cost is visibly incomplete rather than wrong-looking-right.
"""
GALLON_ML = 3785.411784
BEER_BARREL_GAL = 31.0


def _gallons(size_ml):
    return (size_ml or 0) / GALLON_ML


def _federal_excise(rates, beverage_class, abv, gallons, warnings):
    import tax_rates
    row = tax_rates.current(rates, "US", beverage_class, "excise", abv=abv)
    if not row:
        warnings.append("no federal excise for %s (abv=%s)" % (beverage_class, abv))
        return 0.0, None
    rate = float(row["rate_value"])
    basis = row["basis"]
    if basis == "proof_gallon":
        if abv is None:
            warnings.append("spirits federal excise needs ABV — skipped"); return 0.0, row
        qty = gallons * (abv / 100.0 * 2)
    elif basis == "barrel":
        qty = gallons / BEER_BARREL_GAL
    else:  # wine_gallon / gallon
        qty = gallons
    return rate * qty, row


def _state_excise(rates, state, beverage_class, abv, gallons, warnings):
    import tax_rates
    row = tax_rates.current(rates, state, beverage_class, "excise", abv=abv)
    if not row:
        warnings.append("no state excise for %s/%s" % (state, beverage_class)); return 0.0, None
    if row.get("is_control_state"):
        warnings.append("%s is a control state — excise is the state markup; read control_state.py price book" % state)
        return 0.0, row
    if str(row.get("provenance")) == "unverified":
        warnings.append("state excise %s/%s is unverified seed — verify vs DOR" % (state, beverage_class))
    rate = float(row["rate_value"]) if str(row.get("rate_value") or "").strip() else 0.0
    basis = row.get("basis") or "gallon"
    if basis == "liter":
        qty = gallons * 3.785411784
    elif basis == "proof_gallon":
        qty = gallons * ((abv or 0) / 100.0 * 2)
    else:  # gallon / wine_gallon
        qty = gallons
    return rate * qty, row


def landed_cost(base_cost, state, beverage_class, abv=None, size_ml=750,
                include_sales_tax=False, rates=None, on_date=None):
    """Itemized tax stack for one item. Pass `rates` (list from tax_rates.query()/assemble()) to resolve
    many items off one fetch; if omitted it assembles the seed locally. Returns a dict — never raises on a
    missing rate, it accrues `warnings`."""
    import tax_rates
    if rates is None:
        rates, _ = tax_rates.assemble(log=lambda *a: None)
    warnings, lines = [], []
    gallons = _gallons(size_ml)

    fed, fed_row = _federal_excise(rates, beverage_class, abv, gallons, warnings)
    if fed:
        lines.append(dict(kind="federal_excise", amount=round(fed, 4),
                          rate=fed_row["rate_value"], basis=fed_row["basis"], source="TTB"))

    st, st_row = _state_excise(rates, state, beverage_class, abv, gallons, warnings)
    if st:
        lines.append(dict(kind="state_excise", amount=round(st, 4),
                          rate=st_row["rate_value"], basis=st_row.get("basis"),
                          source=st_row.get("source"), provenance=st_row.get("provenance")))

    sales = 0.0
    if include_sales_tax:
        srow = tax_rates.current(rates, state, "all", "sales", on_date=on_date) \
            or tax_rates.current(rates, state, beverage_class, "sales", on_date=on_date)
        if srow and str(srow.get("rate_value") or "").strip():
            pct = float(srow["rate_value"])
            sales = (base_cost + fed + st) * pct / 100.0
            lines.append(dict(kind="sales_tax", amount=round(sales, 4), rate=pct, basis="percent",
                              source=srow.get("source")))
        else:
            warnings.append("no special alcohol sales rate for %s — use the general sales-tax layer" % state)

    total_tax = fed + st + sales
    landed = base_cost + total_tax
    return dict(
        base_cost=round(base_cost, 4),
        federal_excise=round(fed, 4),
        state_excise=round(st, 4),
        sales_tax=round(sales, 4),
        total_tax=round(total_tax, 4),
        landed_cost=round(landed, 4),
        effective_tax_rate=(round(total_tax / base_cost, 4) if base_cost else None),
        tax_per_unit_pretax_basis=round(landed - base_cost, 4),  # subtract from a shelf price to compare pre-tax
        lines=lines,
        state=state, beverage_class=beverage_class, abv=abv, size_ml=size_ml,
        warnings=warnings,
    )


def pretax_price(shelf_price, state, beverage_class, abv=None, size_ml=750, rates=None):
    """Strip the specific excise burden out of an observed shelf price to a pre-tax basis, so a bottle in
    a high-excise state and the same bottle in a low-excise state are comparable — the normalization
    price_signal.py wants ([[price-clustering-signal]]). Sales tax is left in (it's usually not in the
    posted shelf price). Returns {shelf, excise_removed, pretax, warnings}."""
    lc = landed_cost(0.0, state, beverage_class, abv=abv, size_ml=size_ml, rates=rates)
    excise = lc["federal_excise"] + lc["state_excise"]
    return dict(shelf=round(shelf_price, 4), excise_removed=round(excise, 4),
                pretax=round(shelf_price - excise, 4), warnings=lc["warnings"])


if __name__ == "__main__":
    import tax_rates
    rates, _ = tax_rates.assemble(log=lambda *a: None)
    print("Landed-cost demos (federal encoded; state depends on seed coverage):\n")
    demos = [
        ("750ml spirits @ 40% ABV, base $12, TX", dict(base_cost=12, state="TX", beverage_class="spirits", abv=40)),
        ("750ml wine @ 13% ABV, base $8, NY",      dict(base_cost=8,  state="NY", beverage_class="wine", abv=13)),
        ("6x12oz beer (2130ml), base $6, IL",       dict(base_cost=6,  state="IL", beverage_class="beer", size_ml=2130)),
    ]
    for label, kw in demos:
        r = landed_cost(rates=rates, **kw)
        print("• %s" % label)
        print("    fed $%.3f  state $%.3f  total tax $%.3f  landed $%.3f  eff %s%%" % (
            r["federal_excise"], r["state_excise"], r["total_tax"], r["landed_cost"],
            (round(r["effective_tax_rate"] * 100, 1) if r["effective_tax_rate"] is not None else "?")))
        for w in r["warnings"]:
            print("    warn:", w)
