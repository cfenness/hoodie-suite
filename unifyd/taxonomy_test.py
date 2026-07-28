"""The retailer's own category tree is free with the crawl — prove we keep it.

UberEats' catalog walk already descended the hierarchy to collect `sectionUuid`/`subsectionUuid` (needed
as request context for getMenuItemV1) and threw the section TITLES away. So every store crawl paid the
full traversal and discarded the one part that is legible: "Beer > Craft IPA". A breadcrumb is a taxonomy
the retailer maintains and revises for us; dropping it is the same discard-nothing violation as dropping
a field, and it is the cheapest product hierarchy available anywhere in the stack.

Pure parsing — no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ubereats  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append("%s: got %r want %r" % (name, got, want))


# A getStoreV1-shaped payload: titled section nodes (no price) enclosing priced item nodes.
PAYLOAD = {
    "title": "Total Wine — Orlando",
    "catalogSectionsMap": {
        "s-beer": [{
            "uuid": "sec-1", "title": "Beer",
            "payload": {"items": [
                {"uuid": "sub-1", "title": "Craft IPA",
                 "items": [{"uuid": "i-1", "title": "Jai Alai IPA", "price": 1299,
                            "sectionUuid": "sec-1", "subsectionUuid": "sub-1"}]},
                {"uuid": "i-2", "title": "Bud Light 12pk", "price": 1499, "sectionUuid": "sec-1"},
            ]},
        }],
    },
}

idx = ubereats._catalog_index([PAYLOAD])

# uuids still resolve — the enrichment request body depends on them
check("item indexed", idx.get("i-1", {}).get("section"), "sec-1")
check("subsection uuid kept", idx.get("i-1", {}).get("subsection"), "sub-1")

# ...and now the NAMES come with them
check("section name resolved", idx.get("i-1", {}).get("section_name"), "Beer")
check("subsection name resolved", idx.get("i-1", {}).get("subsection_name"), "Craft IPA")
check("shallow item keeps its section", idx.get("i-2", {}).get("section_name"), "Beer")
check("shallow item has no subsection", idx.get("i-2", {}).get("subsection_name"), "")

# A section defined AFTER the items that reference it must still resolve — names are collected in a
# separate first pass precisely so payload ordering cannot silently empty the hierarchy.
LATE = {"items": [{"uuid": "i-9", "title": "Wine X", "price": 999, "sectionUuid": "sec-9"},
                  {"uuid": "sec-9", "title": "Wine"}]}
check("forward reference resolves", ubereats._catalog_index([LATE]).get("i-9", {}).get("section_name"), "Wine")

# A priced node is an ITEM, never a section label — otherwise items name themselves as their own category.
check("priced node is not a section",
      ubereats._catalog_index([{"items": [{"uuid": "i-3", "title": "Thing", "price": 1,
                                           "sectionUuid": "i-3"}]}]).get("i-3", {}).get("section_name"), "")

# The landed schema must actually carry the columns, or the capture never reaches the warehouse.
for col in ("section_name", "subsection_name", "category_path"):
    check("schema carries " + col, col in ubereats.UE_FIELDS, True)

if fails:
    print("\n".join("  FAIL " + f for f in fails))
    print("── %d failed" % len(fails))
    sys.exit(1)
print("── taxonomy: retailer category tree captured, not discarded (11 checks)")
