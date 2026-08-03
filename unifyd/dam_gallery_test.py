"""Exercise the CV reference gallery — P3's "scope-gated" exit criterion.

The gallery must behave correctly in BOTH directions, so this suite proves each: under a prohibiting
record it fetches nothing and derives nothing; under a counsel-cleared grant it fetches, hashes and
embeds. Testing only the hold would leave the grant path unproven, which is how a gate quietly stops
being a gate. Pure stdlib; network and warehouse mocked."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import dam           # noqa: E402
import dam_gallery   # noqa: E402
import rights        # noqa: E402

FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


# ── what counts as a studio reference ─────────────────────────────────────────────────────────────
print("reference classification:")
for name, want in [("BACARDI Superior bottle shot.jpg", True),
                   ("Grey Goose packshot on white.png", True),
                   ("Patron hero cutout.png", True),
                   ("BACARDI logo.svg", False),
                   ("Legacy cocktail competition crowd.jpg", False),
                   ("Distillery portrait of the master blender.jpg", False),
                   ("summer campaign billboard.jpg", False),
                   ("IMG_2043.jpg", False)]:
    _kind, is_ref = dam_gallery.classify_image({"name": name, "folder_path": "Images/2024"})
    check(is_ref is want, "%-46s -> %s" % (name[:46], "reference" if is_ref else "not a reference"))
check(dam_gallery.classify_image({"name": "bottle logo.png"})[1] is False,
      "an exclusion beats an inclusion (a 'bottle logo' is a logo)")

# ── the hold path: a prohibiting record ───────────────────────────────────────────────────────────
print("\nunder a PROHIBITING record:")
PROHIBIT = {"source_id": "t-prohibit", "tos_sha256": "d" * 64,
            "permissions": rights.classify(
                "All rights reserved. You must not use any part of the Materials on our Site for "
                "commercial purposes.")}
ASSETS = [{"asset_id": 1, "asset_url": "https://x/a-bottle-shot.jpg", "name": "BACARDI bottle shot.jpg",
           "folder_path": "Images/2024", "size_bytes": 100, "asset_type": "image",
           "brand": "BACARDÍ", "hoodie_brand_id": "dam:bacardi"},
          {"asset_id": 2, "asset_url": "https://x/b-packshot.jpg", "name": "Grey Goose packshot.jpg",
           "folder_path": "Images/2024", "size_bytes": 200, "asset_type": "image",
           "brand": "GREY GOOSE", "hoodie_brand_id": "dam:grey-goose"},
          {"asset_id": 3, "asset_url": "https://x/c-logo.svg", "name": "logo.svg",
           "folder_path": "Images", "size_bytes": 10, "asset_type": "image"}]

FETCHES = []
_saved_bytes = dam.asset_bytes


def spy_bytes(rec, asset, timeout=60, max_bytes=None):
    FETCHES.append(asset.get("asset_id"))
    return _saved_bytes(rec, asset)          # still goes through the real gate


dam.asset_bytes = spy_bytes


class FakeWarehouse:
    written = {}

    @staticmethod
    def write_accumulate(name, rows, key=None, fields=None, coverage=True):
        FakeWarehouse.written.setdefault(name, []).extend(rows)
        return {"rows": len(rows)}


sys.modules["warehouse"] = FakeWarehouse

rows, cov = dam_gallery.build("t-prohibit", assets=ASSETS, land=True, log=lambda *a: None) \
    if os.path.exists(rights.record_path("t-prohibit")) else (None, None)
if rows is None:
    # No committed record for the synthetic id — exercise the same path with the record injected.
    _saved_load = rights.load
    rights.load = lambda sid: PROHIBIT if sid == "t-prohibit" else _saved_load(sid)
    rows, cov = dam_gallery.build("t-prohibit", assets=ASSETS, land=True, log=lambda *a: None)
    rights.load = _saved_load

check(len(rows) == 2, "only the two product shots became gallery rows (the logo was excluded)")
check(not FETCHES, "ZERO asset bytes were fetched")
check(all(r["phash"] is None for r in rows), "no perceptual hash was derived")
check(all(r["embedding"] is None for r in rows), "no embedding was derived")
check(all(r["retention"] == "pointer_only" for r in rows), "every row is retention=pointer_only")
check(all(r["withheld_reason"] for r in rows), "every row carries WHY it was withheld")
check(all(r["rights_ref"] for r in rows), "every row cites the rights record it was built under")
check(cov["derived"] == 0 and cov["bytes_fetched"] == 0, "the coverage report says nothing was derived")
check(all(set(r) <= set(dam_gallery.GALLERY_FIELDS) for r in rows),
      "every row key is a declared gallery field")

# ── the grant path: a counsel-cleared editorial grant ─────────────────────────────────────────────
print("\nunder a COUNSEL-CLEARED grant:")
GRANT = {"source_id": "t-grant", "tos_sha256": "e" * 64, "counsel_cleared": True,
         "permissions": rights.classify(
             "Images in this press room are made available royalty-free for editorial use by "
             "accredited media.")}
check(GRANT["permissions"]["image_use"] == "permitted", "the fixture record actually grants")

have_pil = True
try:
    import PIL  # noqa: F401
except Exception:
    have_pil = False


def _png():
    """A real, gradient PNG. Generated by pillow rather than pasted as a byte literal — a hand-built
    one was malformed ("broken data stream"), and dhash correctly returned None, so the test read as
    a hashing bug when the FIXTURE was the bug. Without pillow there is nothing to hash anyway."""
    if not have_pil:
        return b"no-pillow-no-image"
    import io as _io
    from PIL import Image as _Image
    im = _Image.new("RGB", (16, 16))
    im.putdata([(x * 15, y * 15, 128) for y in range(16) for x in range(16)])
    buf = _io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


PNG = _png()
check(not have_pil or dam_gallery.dhash(PNG) is not None,
      "the test fixture is a decodable image (else every hash assertion below is meaningless)")
FETCHES.clear()
dam.asset_bytes = lambda rec, asset, timeout=60, max_bytes=None: (
    FETCHES.append(asset.get("asset_id")) or PNG)
_saved_load = rights.load
rights.load = lambda sid: GRANT if sid == "t-grant" else _saved_load(sid)
grows, gcov = dam_gallery.build("t-grant", assets=ASSETS, land=False, log=lambda *a: None)
rights.load = _saved_load
dam.asset_bytes = _saved_bytes

check(len(FETCHES) == 2, "a granted record DOES fetch the product shots (%d)" % len(FETCHES))
check(gcov["bytes_fetched"] == 2, "...and the coverage report says so")
if have_pil:
    check(all(r["phash"] for r in grows), "a perceptual hash was derived for every reference")
    check(all(r["phash_algo"] == "dhash8" for r in grows), "the hash names its algorithm")
    check(all(r["retention"] == "derived" for r in grows), "rows are retention=derived")
    check(gcov["duplicate_images"] == 1,
          "two identical images collapse — the dedupe the pHash is actually for (%d)"
          % gcov["duplicate_images"])
else:
    check(all(r["phash"] is None for r in grows),
          "without pillow no hash is FABRICATED — the row lands NULL")
check(all(r["withheld_reason"] is None for r in grows), "nothing is marked withheld under a grant")

# The embedding backend is absent on this host; the row must say so rather than look vector-less.
check(gcov["embedding_backend"] in ("unavailable", "disabled", "open_clip/ViT-B-32"),
      "the run reports which embedding backend it used (%s)" % gcov["embedding_backend"])
if gcov["embedding_backend"] in ("unavailable", "disabled"):
    check(all(r["embedding"] is None and r["embedding_dim"] == 0 for r in grows),
          "no backend → NULL vectors, reported, never fabricated")

print("\nan UNCLEARED grant is still inert:")
UNCLEARED = dict(GRANT, counsel_cleared=False)
rights.load = lambda sid: UNCLEARED if sid == "t-unc" else _saved_load(sid)
FETCHES.clear()
urows, ucov = dam_gallery.build("t-unc", assets=ASSETS, land=False, log=lambda *a: None)
rights.load = _saved_load
check(not FETCHES, "a grant without counsel_cleared fetches nothing")
check(ucov["derived"] == 0, "...and derives nothing")

# ── the hash primitives ───────────────────────────────────────────────────────────────────────────
print("\nhash primitives:")
check(dam_gallery.dhash(b"not an image") is None, "undecodable bytes yield NO hash, not a fake one")
check(dam_gallery.hamming("ffffffffffffffff", "ffffffffffffffff") == 0, "identical hashes distance 0")
check(dam_gallery.hamming("0000000000000000", "ffffffffffffffff") == 64, "opposite hashes distance 64")
check(dam_gallery.hamming(None, "ffffffffffffffff") is None,
      "a MISSING hash is not distance 0 — an absent hash must never read as a perfect match")
check(dam_gallery.hamming("abc", "abcd") is None, "mismatched lengths yield None")

print("\n%s (%d failure%s)" % ("FAILED" if FAILS else "PASSED", len(FAILS), "" if len(FAILS) == 1 else "s"))
sys.exit(1 if FAILS else 0)
