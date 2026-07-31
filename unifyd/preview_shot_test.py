"""Offline test for the Live Preview screenshot+diff module: python unifyd/preview_shot_test.py

capture() needs a real browser (channel="chrome") and is deliberately NOT unit-tested here — same
precedent as browser_warm.Warmer, which has no paired test file either. Everything else (the URL
guard, the diff math, the index/retention bookkeeping) is pure and fully covered with real Pillow
images (small, generated in-memory) and injected put_bytes/get_bytes fakes — no real warehouse,
no real browser.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import preview_shot as ps

passed = failed = 0
def ok(n, c):
    global passed, failed
    if c: passed += 1; print("  ok   %s" % n)
    else: failed += 1; print("  FAIL %s" % n)
def eq(n, got, want): ok("%s (got %r)" % (n, got), got == want)


def _png(color, size=(40, 40)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


print("URL guard — the metadata endpoint is the one thing that must never be reachable")
ok("http is fine", ps.ok_preview_url("http://localhost:8937/apps/cockpit.html")[0])
ok("localhost is explicitly ALLOWED — the main intended use is a local dev server", ps.ok_preview_url("http://localhost:3000")[0])
ok("a private IP is allowed too (same reasoning)", ps.ok_preview_url("http://192.168.1.5:8080")[0])
o, r = ps.ok_preview_url("http://169.254.169.254/latest/meta-data/")
ok("the AWS/Fly metadata IP is blocked", not o)
ok("...with a clear reason", "metadata" in r)
o, r = ps.ok_preview_url("http://metadata.google.internal/x")
ok("the GCP metadata host is blocked", not o)
o, r = ps.ok_preview_url("ftp://example.com/x")
ok("non-http(s) scheme rejected", not o)
o, r = ps.ok_preview_url("not a url at all")
ok("garbage is rejected, not crashed on", not o)

print("\nkey validation — the only gate on an authenticated user reading an arbitrary warehouse key")
good_key = ps._shot_key("http://example.com", 1785000000)
ok("a real generated key validates", ps.valid_key(good_key))
ok("a path-traversal attempt is rejected", not ps.valid_key("../../_deploy_expected.json"))
ok("an unrelated warehouse key is rejected", not ps.valid_key("label_images/12345.jpg"))
ok("empty is rejected", not ps.valid_key(""))
ok("None is rejected, not crashed on", not ps.valid_key(None))

print("\nsave() / list_snapshots() — index bookkeeping, all injected (no real warehouse)")
store = {}
def put(k, v): store[k] = v
def get(k): return store.get(k)
url = "http://localhost:3000/"
r1 = ps.save(url, _png((255, 0, 0)), ts=100, put_bytes_fn=put, get_bytes_fn=get)
eq("first save returns its ts", r1["ts"], 100)
ok("the key matches the expected pattern", ps.valid_key(r1["key"]))
snaps = ps.list_snapshots(url, get_bytes_fn=get)
eq("one snapshot recorded", len(snaps), 1)

ps.save(url, _png((0, 255, 0)), ts=200, put_bytes_fn=put, get_bytes_fn=get)
snaps = ps.list_snapshots(url, get_bytes_fn=get)
eq("two snapshots now", len(snaps), 2)
eq("newest first", snaps[0]["ts"], 200)
eq("...then the older one", snaps[1]["ts"], 100)

print("\nretention — a disclosed cap, not a silent drop")
for i in range(ps.MAX_SNAPSHOTS + 5):
    ps.save(url, _png((i % 256, 0, 0)), ts=1000 + i, put_bytes_fn=put, get_bytes_fn=get)
snaps = ps.list_snapshots(url, get_bytes_fn=get)
eq("capped at MAX_SNAPSHOTS", len(snaps), ps.MAX_SNAPSHOTS)
eq("keeps the NEWEST ones, not the oldest", snaps[0]["ts"], 1000 + ps.MAX_SNAPSHOTS + 4)

print("\nload_shot() refuses anything that isn't a valid generated key")
eq("a bad key returns None without ever calling get_bytes_fn",
   ps.load_shot("../../secrets.json", get_bytes_fn=lambda k: (_ for _ in ()).throw(AssertionError("must not be called"))),
   None)
store2 = {good_key: b"pngbytes"}
eq("a valid key round-trips", ps.load_shot(good_key, get_bytes_fn=store2.get), b"pngbytes")

print("\ndiff() — identical images")
red = _png((200, 60, 60))
d = ps.diff(red, red)
eq("0% changed", d["diff_pct"], 0.0)
eq("no bbox", d["bbox"], None)
ok("not flagged as resized", not d["resized"])
ok("overlay is a real PNG", d["overlay_png"][:8] == b"\x89PNG\r\n\x1a\n")

print("\ndiff() — a real change (half the image recolored)")
from PIL import Image
img_a = Image.new("RGB", (40, 40), (255, 255, 255))
img_b = img_a.copy()
for x in range(20, 40):
    for y in range(40):
        img_b.putpixel((x, y), (255, 0, 0))
buf_a, buf_b = io.BytesIO(), io.BytesIO()
img_a.save(buf_a, format="PNG"); img_b.save(buf_b, format="PNG")
d = ps.diff(buf_a.getvalue(), buf_b.getvalue())
ok("roughly half the pixels changed (%.1f%%)" % d["diff_pct"], 45 <= d["diff_pct"] <= 55)
ok("bbox covers the right half", d["bbox"][0] >= 19)

print("\ndiff() — near-identical (anti-aliasing jitter) reads as 0%, not noise")
img_c = img_a.copy()
img_c.putpixel((5, 5), (252, 253, 254))     # a 2-3 value wobble, well under the threshold
buf_c = io.BytesIO(); img_c.save(buf_c, format="PNG")
d = ps.diff(buf_a.getvalue(), buf_c.getvalue())
eq("sub-threshold noise is NOT counted as changed", d["diff_pct"], 0.0)

print("\ndiff() — mismatched sizes are cropped to the shared region, never silently stretched")
img_d = Image.new("RGB", (60, 60), (255, 255, 255))
buf_d = io.BytesIO(); img_d.save(buf_d, format="PNG")
d = ps.diff(buf_a.getvalue(), buf_d.getvalue())
ok("flagged as resized", d["resized"])
eq("compared over the shared 40x40 region", tuple(d["size"]), (40, 40))

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
