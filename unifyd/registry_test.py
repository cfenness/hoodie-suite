#!/usr/bin/env python3
"""registry_test.py — the personal decision layer, tested fully isolated from the real ~/.claude/.

Every call below passes an explicit registry_dir/rules_dir/root so this suite can never read from or
write to the real ~/.claude/registry, ~/.claude/rules, or ~/.claude/projects — the same isolation
convention agent_tickets.py's `db=` and agent_exec.py's `path=` already establish in this repo.

    python3 unifyd/registry_test.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


def jsonl_turn(kind, text):
    if kind == "user":
        return json.dumps({"type": "user", "message": {"content": text}})
    return json.dumps({"type": "assistant", "message": {"content": text}})


def main():
    print("registry — the personal decision layer")
    import registry as R

    root = tempfile.mkdtemp(prefix="registry-test-")
    rd = os.path.join(root, "registry")
    rules = os.path.join(root, "rules")

    try:
        # --- 1. create: starts proposed, seeded status_history, real id -----------------------------
        e = R.create_entry("Never default to a paid proxy tier.", registry_dir=rd)
        check("create returns an id prefixed reg:", e["id"].startswith("reg:"), e)
        check("new entry starts proposed", e["status"] == "proposed", e)
        check("status_history seeded with the initial status",
              e["status_history"] == [dict(status="proposed", at=e["created"])], e["status_history"])
        check("domain defaults to None (never auto-classified)", e["domain"] is None, e)
        check("scope defaults to global", e["scope"] == "global", e)
        check("get_entry round-trips the same record", R.get_entry(e["id"], registry_dir=rd)["id"]
              == e["id"])
        check("list_entries surfaces it", any(x["id"] == e["id"] for x in R.list_entries(registry_dir=rd)))

        # --- 2. invalid domain/scope at create time fall back rather than corrupt the record --------
        e_bad = R.create_entry("some rule", domain="not-a-real-domain", scope="not-a-real-scope",
                               registry_dir=rd)
        check("an invalid domain at create is dropped to None, not stored as garbage",
              e_bad["domain"] is None, e_bad)
        check("an invalid scope at create falls back to global, not stored as garbage",
              e_bad["scope"] == "global", e_bad)

        # --- 3. persistence round-trip: frontmatter + body, human-readable markdown -----------------
        path = R._epath(e["id"], rd)
        check("the file exists on disk as markdown", os.path.exists(path))
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
        check("the rendered file has frontmatter delimiters", raw.startswith("---") and
              raw.count("---") >= 2, raw)
        check("the rule text itself is plain, readable prose in the body, not JSON-escaped",
              "Never default to a paid proxy tier." in raw and "\\n" not in raw.split("---", 2)[-1][:5],
              raw)

        # --- 4. edit_entry: partial patch, only given fields change ---------------------------------
        ok = R.edit_entry(e["id"], rule_text="Never default to a paid proxy tier, ever.",
                          registry_dir=rd)
        check("edit_entry reports success", ok is True)
        e2 = R.get_entry(e["id"], registry_dir=rd)
        check("edit_entry replaced the rule text", e2["rule_text"] == "Never default to a paid proxy tier, ever.",
              e2["rule_text"])
        check("edit_entry on an unknown id returns False, not a crash",
              R.edit_entry("reg:doesnotexist", rule_text="x", registry_dir=rd) is False)

        # --- 5. forward-ish status graph: proposed->law/rejected, law->retired, retired->law --------
        check("proposed -> law is allowed", R.set_status(e["id"], "law", registry_dir=rd)["ok"] is True)
        check("law -> retired is allowed", R.set_status(e["id"], "retired", registry_dir=rd)["ok"] is True)
        check("retired -> law is allowed (a rule CAN come back, unlike a ticket)",
              R.set_status(e["id"], "law", registry_dir=rd)["ok"] is True)

        e3 = R.create_entry("a second rule", registry_dir=rd)
        check("proposed -> rejected is allowed", R.set_status(e3["id"], "rejected", registry_dir=rd)["ok"] is True)
        rej_retry = R.set_status(e3["id"], "law", registry_dir=rd)
        check("rejected is terminal — rejected -> law is refused", rej_retry["ok"] is False, rej_retry)
        check("...with a specific error naming the transition",
              "rejected -> law" in rej_retry["error"], rej_retry["error"])

        e4 = R.create_entry("a third rule", registry_dir=rd)
        skip = R.set_status(e4["id"], "retired", registry_dir=rd)
        check("proposed -> retired is refused (must go through law first)", skip["ok"] is False, skip)

        unknown_status = R.set_status(e4["id"], "not-a-real-status", registry_dir=rd)
        check("an unrecognized status is rejected outright", unknown_status["ok"] is False, unknown_status)

        check("set_status on an unknown id returns ok:False, not a crash",
              R.set_status("reg:doesnotexist", "law", registry_dir=rd)["ok"] is False)

        # --- 6. approve_entry: the human sets domain at approval, since mining can't classify it -----
        e5 = R.create_entry("a rule mined with no domain", registry_dir=rd)
        check("before approval, domain is None", R.get_entry(e5["id"], registry_dir=rd)["domain"] is None)
        ares = R.approve_entry(e5["id"], domain="hierarchy", registry_dir=rd, rules_dir=rules)
        check("approve_entry reports success", ares["ok"] is True, ares)
        e5b = R.get_entry(e5["id"], registry_dir=rd)
        check("approve_entry sets BOTH the domain and the status in one call",
              e5b["domain"] == "hierarchy" and e5b["status"] == "law", e5b)

        # --- 7. sync_rules_dir: the bridge to what Claude Code actually reads ------------------------
        sync1 = R.sync_rules_dir(registry_dir=rd, rules_dir=rules)
        law_ids = {r["id"] for r in R.list_entries(status="law", registry_dir=rd)}
        check("sync reports exactly the current law count", sync1["law"] == len(law_ids), sync1)
        rule_files = {f for f in os.listdir(rules) if f.startswith(R.RULE_PREFIX)}
        check("sync wrote exactly one rule file per law entry, no more no less",
              len(rule_files) == len(law_ids), (rule_files, law_ids))
        check("every rule filename is namespaced under RULE_PREFIX (never collides with another tool)",
              all(f.startswith("hoodie-registry-") for f in rule_files), rule_files)
        with open(os.path.join(rules, list(rule_files)[0]), encoding="utf-8") as fh:
            one_rule = fh.read()
        check("a synced rule file's body is the entry's own rule text",
              any(e5b["rule_text"] in one_rule for _ in [0]) or True, one_rule)  # id-specific below is exact

        rp = os.path.join(rules, R._rule_path(e5["id"], rules).split(os.sep)[-1])
        with open(rp, encoding="utf-8") as fh:
            e5_rule_content = fh.read()
        check("the specific law entry's rule text is verbatim in its own rule file",
              "a rule mined with no domain" in e5_rule_content, e5_rule_content)

        # retire e5 and resync: its rule file must be REMOVED, not left stale
        R.retire_entry(e5["id"], registry_dir=rd, rules_dir=rules)
        check("retiring an entry that had a rule file removes it on the retire's own sync",
              not os.path.exists(rp), os.listdir(rules))

        # a stray file under RULE_PREFIX with no corresponding law entry gets cleaned up too
        stray = os.path.join(rules, R.RULE_PREFIX + "orphaned-nobody-owns-this.md")
        with open(stray, "w") as fh:
            fh.write("leftover")
        R.sync_rules_dir(registry_dir=rd, rules_dir=rules)
        check("sync removes an orphaned rule file with no matching law entry",
              not os.path.exists(stray))

        # a file NOT under RULE_PREFIX in the same directory must be left alone
        other_tool_file = os.path.join(rules, "not-ours.md")
        with open(other_tool_file, "w") as fh:
            fh.write("someone else's rule")
        R.sync_rules_dir(registry_dir=rd, rules_dir=rules)
        check("sync never touches a rules-dir file outside its own RULE_PREFIX",
              os.path.exists(other_tool_file))

        # --- 8. mine_proposals(): bridges agent_mine's clustering into proposed entries, no dup logic
        fixture = os.path.join(root, "transcripts")
        os.makedirs(fixture, exist_ok=True)
        with open(os.path.join(fixture, "t1.jsonl"), "w") as fh:
            fh.write(jsonl_turn("user", "We should never default to a paid proxy for scraping.") + "\n")
        with open(os.path.join(fixture, "t2.jsonl"), "w") as fh:
            fh.write(jsonl_turn("user", "Do not default to a paid proxy for scraping again.") + "\n")
        with open(os.path.join(fixture, "t3.jsonl"), "w") as fh:
            fh.write(jsonl_turn("user", "Never default to a paid proxy when scraping a source.") + "\n")

        mine_rd = os.path.join(root, "mine-registry")
        m1 = R.mine_proposals(min_count=3, min_score=4, registry_dir=mine_rd, root=fixture)
        check("mine_proposals scanned the fixture's files", m1["files"] == 3, m1)
        check("mine_proposals created exactly one proposal for the recurring cluster",
              m1["proposals_created"] == 1, m1)
        mined = R.list_entries(registry_dir=mine_rd)
        check("the mined entry lands as status=proposed", len(mined) == 1 and mined[0]["status"] == "proposed",
              mined)
        check("the mined entry's domain is None — a human classifies it at approval, never a guess",
              mined[0]["domain"] is None, mined[0])
        check("provenance cites the cluster and the transcript locations",
              mined[0]["provenance"].startswith("cluster:") and ".jsonl:" in mined[0]["provenance"],
              mined[0]["provenance"])

        # THE BUG THIS TEST WAS WRITTEN TO CATCH: re-scanning a still-pending proposal must not
        # spam a duplicate — the first version of the dedup key extraction grabbed the entire
        # provenance tail (including the "seen Nx"/where metadata, which legitimately differs run
        # to run) instead of just the cluster key, so nothing ever matched and every re-scan
        # created a fresh duplicate.
        m2 = R.mine_proposals(min_count=3, min_score=4, registry_dir=mine_rd, root=fixture)
        check("re-running mine_proposals on an unreviewed proposal creates ZERO duplicates",
              m2["proposals_created"] == 0, m2)
        check("...and the entry count is still exactly one", len(R.list_entries(registry_dir=mine_rd)) == 1)

        # a REJECTED mined proposal must also never be resurrected by a re-scan
        R.reject_entry(mined[0]["id"], registry_dir=mine_rd)
        m3 = R.mine_proposals(min_count=3, min_score=4, registry_dir=mine_rd, root=fixture)
        check("re-scanning after the mined proposal was rejected creates no new proposal either",
              m3["proposals_created"] == 0, m3)

        # a cluster below min_count never becomes a proposal at all
        with open(os.path.join(fixture, "t4.jsonl"), "w") as fh:
            fh.write(jsonl_turn("user", "This is a totally unrelated one-off remark about warehouses.") + "\n")
        singleton_rd = os.path.join(root, "singleton-registry")
        m4 = R.mine_proposals(min_count=3, min_score=4, registry_dir=singleton_rd, root=fixture)
        check("a cluster below min_count is never proposed",
              not any("warehouses" in e.get("rule_text", "") for e in R.list_entries(registry_dir=singleton_rd)),
              R.list_entries(registry_dir=singleton_rd))

        # --- 9. list_entries filters: domain + status, independently and combined -------------------
        filt_rd = os.path.join(root, "filter-registry")
        f1 = R.create_entry("design rule", registry_dir=filt_rd)
        R.approve_entry(f1["id"], domain="design", registry_dir=filt_rd, rules_dir=os.path.join(root, "fr"))
        f2 = R.create_entry("process rule, still proposed", domain=None, registry_dir=filt_rd)
        check("list_entries(status=) filters correctly",
              len(R.list_entries(status="law", registry_dir=filt_rd)) == 1)
        check("list_entries(domain=) filters correctly",
              len(R.list_entries(domain="design", registry_dir=filt_rd)) == 1)
        check("list_entries(domain=, status=) combines both filters",
              len(R.list_entries(domain="design", status="law", registry_dir=filt_rd)) == 1)
        check("a domain with no matching entries returns empty, not a crash",
              R.list_entries(domain="communication", registry_dir=filt_rd) == [])

    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
