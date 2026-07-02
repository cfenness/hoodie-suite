# Deploy the whole suite on Fly.io (one origin, no AWS)

The AWS path (private S3 + CloudFront static, ECS-Express `/api/*` backend) is fully built but blocked
on new-account verification. This is the fast alternative: **one Fly app serves the static suite AND
the `/api/*` backend from a single origin**, so the apps' same-origin `/api/*` fetches work with no
CORS and no separate frontend host. The engine source is in the image but is never web-served —
`server.py` only serves an allowlist of public files (`_SUITE_OK_TOP`), mirroring the deploy excludes.

## One-time

```bash
# 1. Install + log in (once)
curl -L https://fly.io/install.sh | sh        # or: brew install flyctl
fly auth login

# 2. Create the app from the root Dockerfile (picks up ./fly.toml; choose a unique app name)
fly launch --no-deploy

# 3. Secrets — enable the AI features; the key never touches the browser
fly secrets set ANTHROPIC_API_KEY=sk-ant-...          # overlay AI read, planogram vision + pitch, self-heal
fly secrets set AGENT_TOKEN=$(openssl rand -hex 24)   # optional: gate /api/* for non-browser callers
# optional pull sources:
# fly secrets set BRIGHTDATA_API_KEY=... AGENT_SELF_HEAL=1

# 4. Ship it
fly deploy
fly open                                              # the live launcher
curl "$(fly info -j | python3 -c 'import sys,json;print(json.load(sys.stdin)["Hostname"])')/api/health"
```

## Notes

- **One machine, pinned.** Run state (pulled datasets/runs) is in-process, so `fly.toml` sets
  `min_machines_running = 1` and `auto_stop_machines = "off"`. Don't scale to N — the state would fork.
  For durable state across redeploys, set `STATE_BUCKET` (needs S3 creds on the machine) — same
  `save()/load()` abstraction as the AWS path.
- **Health check** hits `/api/health`; give it ~20s grace on cold boot.
- **Redeploys:** `fly deploy` after any change. (Or wire a Fly GitHub Action later.)
- **The AWS path still works** when the account clears — it's independent of this. Use whichever origin
  you want; the apps don't care (same-origin `/api/*` either way).
