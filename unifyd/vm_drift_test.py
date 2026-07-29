"""Unit test for the fly.toml [[vm]] drift check: python unifyd/vm_drift_test.py

The unit mismatch is the whole trap. fly.toml writes `memory = "8gb"`; the CLI and machine listing
report `8192`. A naive comparison reports drift on every deploy, so it gets ignored within a day and
is worse than no check. These pin the normalisation.
"""
import os
import sys
import importlib.util

spec = importlib.util.spec_from_file_location(
    "rt", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "tools", "release_train.py"))
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)

passed = failed = 0
def ok(n, c):
    global passed, failed
    if c: passed += 1; print("  ok   %s" % n)
    else: failed += 1; print("  FAIL %s" % n)
def eq(n, got, want): ok("%s (got %r)" % (n, got), got == want)

print("memory normalisation — fly.toml units vs CLI units")
eq('"8gb" is 8192 MB, not drift vs 8192', rt.mem_mb("8gb"), 8192)
eq('"4gb"', rt.mem_mb("4gb"), 4096)
eq('"8192mb"', rt.mem_mb("8192mb"), 8192)
eq('bare 8192 (CLI form)', rt.mem_mb(8192), 8192)
eq('"8 GB" with space and caps', rt.mem_mb("8 GB"), 8192)
eq('"2gb"', rt.mem_mb("2gb"), 2048)
eq('"0.5gb"', rt.mem_mb("0.5gb"), 512)
eq('None stays None', rt.mem_mb(None), None)
eq('garbage stays None', rt.mem_mb("lots"), None)
ok("the trap: '8gb' and 8192 compare EQUAL after normalisation",
   rt.mem_mb("8gb") == rt.mem_mb(8192))
ok("...and a real difference still shows", rt.mem_mb("4gb") != rt.mem_mb("8gb"))

print("\nsize string -> cpus/kind")
for size, cpus, kind in [("shared-cpu-1x", 1, "shared"), ("shared-cpu-2x", 2, "shared"),
                         ("shared-cpu-4x", 4, "shared"), ("performance-2x", 2, "performance")]:
    eq(size, rt._SIZE_CPUS[size], (cpus, kind))

print("\ndeclared_vm() reads the real hoodie-suite fly.toml")
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
vm = rt.declared_vm(root)
ok("a [[vm]] block was found", vm is not None)
if vm:
    eq("size", vm["size"], "shared-cpu-4x")
    eq("cpus derived from size", vm["cpus"], 4)
    eq("memory normalised to MB", vm["memory_mb"], 8192)
eq("no fly.toml -> None", rt.declared_vm("/tmp"), None)

print("\nthe Hoodie Relations case, reproduced")
# fly.toml said shared-cpu-2x/4gb; the machine had been raised to shared-cpu-4x/8192 by hand.
declared_mem, running_mem = rt.mem_mb("4gb"), 8192
ok("the deploy would SHRINK 8192 -> 4096", declared_mem < running_mem)
eq("...by exactly the observed amount", (running_mem, declared_mem), (8192, 4096))
# and after they pinned it:
ok("pinning fly.toml to 8gb removes the drift", rt.mem_mb("8gb") == running_mem)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
