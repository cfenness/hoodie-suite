#!/usr/bin/env python3
"""resi_isp_exclude_test.py — ISP_PROXIES_EXCLUDE drops known-bad hosts from the pool, no network.

Pins the real live incident (2026-08-03): after Webshare reloaded its list (user bought 50 more IPs,
enabled high concurrency), 2 of 100 fresh IPs (192.241.92.34, 192.241.92.241) were consistently 0%
success (exit_pattern=burned_subset) on two independent DoorDash shards while every other IP in the
same batch was ~100% — a provider-side dead-endpoint issue, not a credential problem (same creds
worked fine on the other 98). ISP_PROXIES_EXCLUDE lets a known-bad host be dropped from the pool
without touching the provider-managed ISP_PROXIES secret itself.

    python3 unifyd/resi_isp_exclude_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


def main():
    import resi

    real_isp_proxies = os.environ.get("ISP_PROXIES")
    real_exclude = os.environ.get("ISP_PROXIES_EXCLUDE")
    real_policy = os.environ.get("FETCH_POLICY")
    real_file = os.environ.get("ISP_PROXIES_FILE")
    os.environ["FETCH_POLICY"] = "flat"                 # isp_allowed() must see the flat-rate tier
    # isp_pool() ALSO merges a file (default: isp_proxies.txt next to resi.py) — a real one may exist
    # on whatever machine runs this, so point at a path that can't exist to isolate the test to
    # exactly the ISP_PROXIES env entries below.
    os.environ["ISP_PROXIES_FILE"] = "/nonexistent/isp_proxies_test_isolation.txt"

    # The exact live incident: 4 hosts including the 2 real burned ones from 2026-08-03.
    os.environ["ISP_PROXIES"] = (
        "192.241.92.34:8080:wnrndejc:rpcgaz7927n9,"
        "192.241.92.241:8080:wnrndejc:rpcgaz7927n9,"
        "45.56.136.128:8560:wnrndejc:rpcgaz7927n9,"
        "9.142.39.42:6000:wnrndejc:rpcgaz7927n9")

    try:
        print("no exclusion set -> the full pool, including the burned hosts")
        os.environ.pop("ISP_PROXIES_EXCLUDE", None)
        pool = resi.isp_pool()
        check("all 4 hosts present with no exclusion", len(pool) == 4, pool)

        print("\nISP_PROXIES_EXCLUDE drops exactly the named hosts, by host only")
        os.environ["ISP_PROXIES_EXCLUDE"] = "192.241.92.34,192.241.92.241"
        pool2 = resi.isp_pool()
        check("the 2 burned hosts are gone", len(pool2) == 2, pool2)
        check("the 2 healthy hosts survive", all("45.56.136.128" in p or "9.142.39.42" in p for p in pool2),
              pool2)
        check("neither burned host survives under any port/session pairing",
              not any("192.241.92.34" in p or "192.241.92.241" in p for p in pool2), pool2)

        print("\nsemicolon and newline separators both work, matching ISP_PROXIES' own format")
        os.environ["ISP_PROXIES_EXCLUDE"] = "192.241.92.34;192.241.92.241"
        check("semicolon-separated exclusion works", len(resi.isp_pool()) == 2, resi.isp_pool())
        os.environ["ISP_PROXIES_EXCLUDE"] = "192.241.92.34\n192.241.92.241"
        check("newline-separated exclusion works", len(resi.isp_pool()) == 2, resi.isp_pool())

        print("\nan empty/unset exclusion is a no-op — this is opt-in, not a new default filter")
        os.environ["ISP_PROXIES_EXCLUDE"] = ""
        check("empty string excludes nothing", len(resi.isp_pool()) == 4, resi.isp_pool())
        os.environ.pop("ISP_PROXIES_EXCLUDE", None)
        check("unset excludes nothing", len(resi.isp_pool()) == 4, resi.isp_pool())

        print("\nexcluding a host not in the pool is harmless, not an error")
        os.environ["ISP_PROXIES_EXCLUDE"] = "1.2.3.4"
        check("excluding an absent host changes nothing", len(resi.isp_pool()) == 4, resi.isp_pool())
    finally:
        for k, v in (("ISP_PROXIES", real_isp_proxies), ("ISP_PROXIES_EXCLUDE", real_exclude),
                    ("FETCH_POLICY", real_policy), ("ISP_PROXIES_FILE", real_file)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
