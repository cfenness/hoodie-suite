"""Exercise the canonical hierarchy: that it IS a hierarchy, and that it grows correctly.

The failure mode this guards is quiet: a taxonomy that looks fine but places a term at the wrong
level, or learns a leaf with no parent so it filters under everything. Pure stdlib; the warehouse is
mocked."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import product_taxonomy as pt  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


print("the seed is a hierarchy, not four lists:")
t = pt.SEED
check("Spirits" in t and "Wine" in t and "Beer" in t, "the top level is Product Type")
check("Bourbon" in t["Spirits"]["Whiskey"],
      "Bourbon is a Sub Class under Whiskey — not a Type, which is how it drifts when the levels "
      "are free text")
check("Whiskey" not in t, "...and Whiskey is not a Type")
check("Cabernet Sauvignon" in t["Wine"]["Still Wine"]["Red Wine"],
      "a grape is a VARIETAL, at the fourth level")
check("Cabernet Sauvignon" not in t["Wine"]["Still Wine"],
      "...and never a Sub Class")

names = {}
for ty, cls in t.items():
    for cl, subs in cls.items():
        names.setdefault(cl, set()).add(("class", ty))
        for sc in subs:
            names.setdefault(sc, set()).add(("sub", ty))
dupes = {k: v for k, v in names.items() if len({lvl for lvl, _ in v}) > 1}
check(not dupes, "no term sits at two different LEVELS (%s)" % (sorted(dupes) or "none"))

print("\nvarietal is not universal:")
check("Wine" in pt.VARIETAL_TYPES, "wine has varietals")
check("Spirits" not in pt.VARIETAL_TYPES,
      "spirits do not — 'Reposado' is an age statement, not a grape")
check(all(not v for subs in t["Spirits"].values() for v in subs.values()),
      "and every spirits sub class carries an EMPTY varietal list, so the surface can say "
      "'not applicable' rather than 'no values yet'")

print("\nthe basis of each branch is stated:")
check(set(pt.BASIS) == set(t), "every Type records what its levels are grounded in")
check("5.22" in pt.BASIS["Spirits"] and "4.21" in pt.BASIS["Wine"],
      "the two regulated branches cite the CFR part")
check("trade convention" in pt.BASIS["Beer"],
      "the beer branch says it is trade convention — TTB defines no styles, and presenting a style "
      "argument as a federal class invites an argument about the wrong thing")

print("\nlearning a path:")
LANDED = []


class FakeWarehouse:
    rows = []

    @staticmethod
    def query(name, sql=None, params=None):
        return list(FakeWarehouse.rows)

    @staticmethod
    def write_accumulate(name, rows, key=None, fields=None, coverage=True):
        LANDED.extend(rows)
        FakeWarehouse.rows.extend(rows)
        return {"rows": len(rows)}


sys.modules["warehouse"] = FakeWarehouse
check(pt.learn({"canon_type": "Spirits", "canon_class": "Whiskey",
                "canon_subclass": "Kentucky Straight Rye"}, log=lambda *a: None) == 1,
      "a new sub class is learned")
row = LANDED[-1]
check(row["canon_type"] == "Spirits" and row["canon_class"] == "Whiskey",
      "the WHOLE path is recorded, not the leaf — a sub class with no parent cannot be filtered "
      "under any class, so it would surface under every class forever")
check(pt.learn({"canon_subclass": "Orphan"}, log=lambda *a: None) == 0,
      "a value with no Type is refused — there is nothing to hang it from")
check(pt.learn({"canon_type": "Spirits"}, log=lambda *a: None) == 0,
      "a Type on its own teaches no hierarchy")

built = pt.tree(log=lambda *a: None)
check("Kentucky Straight Rye" in built["tree"]["Spirits"]["Whiskey"],
      "a learned node is merged into the served tree at its own level")
check("Kentucky Straight Rye" not in pt.SEED["Spirits"]["Whiskey"],
      "...without mutating the seed")
check(built["learned_paths"] == 1, "and the count of learned paths is reported (%s)"
      % built["learned_paths"])

pt.learn({"canon_type": "Wine", "canon_class": "Still Wine", "canon_subclass": "Red Wine",
          "canon_varietal": "Blaufränkisch"}, log=lambda *a: None)
built = pt.tree(log=lambda *a: None)
check("Blaufränkisch" in built["tree"]["Wine"]["Still Wine"]["Red Wine"],
      "a learned varietal lands under its own sub class, not globally")
check("Blaufränkisch" not in built["tree"]["Wine"]["Still Wine"]["White Wine"],
      "...and does NOT appear under a sibling")

print("\nan unreadable table:")


class Dead:
    @staticmethod
    def query(*a, **k):
        raise RuntimeError("no table")


sys.modules["warehouse"] = Dead
d = pt.tree(log=lambda *a: None)
check(d["tree"] and d["learned_paths"] == 0,
      "the seed still serves when nothing has been learned yet — an empty tree would silently turn "
      "every dropdown into free text")

print("\n%s (%d failure%s)" % ("FAILED" if FAILS else "PASSED", len(FAILS), "" if len(FAILS) == 1 else "s"))
sys.exit(1 if FAILS else 0)
