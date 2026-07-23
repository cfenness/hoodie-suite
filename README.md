# Hoodie Suite

One front door for every Hoodie / Prism / Unifyd app. `index.html` is a launcher
shell with a persistent sidebar; each app lives in `apps/` and opens in-frame (with
an "Open full ↗" escape hatch). Pushing to `main` deploys the whole thing to S3.

```
.
├── index.html                 # the suite shell (launcher + spine host)
├── suite.css                  # shared chrome token contract
├── suite-header.js            # in-app suite header strip (standalone apps)
├── apps/                      # the static surfaces (render targets)
│   ├── dashboard.html         # Hoodie Intelligence (Prism)
│   ├── crm.html               # Hoodie Relations — deal-qualification CRM (MEDDPICC/Gap)
│   ├── presenter.html         # Speaker's workbench
│   ├── mdm.html               # Hoodie MDM — one console (14 sections: Master workbench · Steward · Catalog · Outlets · Sources · …)
│   ├── ttb-ingestion.html     # TTB COLA ingestion view (Unifyd)
│   ├── pulls.html             # Pulls tab — run & evaluate each scrape (Unifyd · /api/run + /api/runs)
│   ├── training-suite.html    # The Bench — five training rooms
│   ├── sales-tutorial.html    # The Long Game — sales room
│   ├── roadmap.html           # Product roadmap
│   ├── principles-hub.html    # Principles & Architecture KB
│   ├── spine-adapter.html     # Reference: how apps join the spine
│   └── _archive/              # superseded surfaces (kept, not referenced — see its README)
├── spine/
│   ├── spine.js               # the shared backbone (host + connect)
│   └── hierarchy.sample.json  # canonical hierarchy (sample)
├── unifyd/                    # the ingestion ENGINE — scrapers + local agent (NOT deployed)
│   ├── server.py              #   local agent (Flask) — serves hoodie_mdm.html + runs real pulls
│   ├── ttb_cola_scraper.py    #   TTB COLA registry scraper
│   ├── abc_fws_scraper.py     #   ABC FWS directional inventory tracker (BigCommerce, polite)
│   ├── pull_sources.py        #   batch puller (Florida + COLA)
│   ├── schedule_pull.py       #   run any pull on a cadence locally (pre-backend)
│   ├── hoodie_mdm.html        #   the MDM control plane the agent serves
│   ├── requirements.txt
│   ├── fixtures/              #   captured TTB pages for parser confirmation
│   └── README.md              #   engine docs (run instructions, provenance)
├── cloudfront/basic-auth.js   # optional shared-password gate
├── .github/workflows/deploy.yml  # CI: push to main → deploy
├── deploy.sh                  # manual deploy from your laptop
├── CLAUDE.md · SPINE.md       # internal docs (not shipped)
└── README.md
```

To add an app later: drop the file in `apps/`, then add one line to the `APPS`
array near the top of `index.html`. That's the whole integration.

> **The Unifyd engine (`unifyd/`) is the owned layer, and it does not ship.** It is
> excluded from the CloudFront deploy along with `*.py`, `cloudfront/`, and the
> internal docs (see `deploy.sh` and `.github/workflows/deploy.yml`). The static
> apps in `apps/` are render targets; `unifyd/` is where ingestion actually happens.
> See `unifyd/README.md` to run the agent and the scraper.

---

## Develop locally (the fast loop)

One command. No build, no deploy, no file shuffling — edit any HTML and refresh.

```bash
./dev.sh                 # serve the whole suite on http://localhost:8000
                         # (also starts the Unifyd agent so /api/* is live, if its deps are installed)
./dev.sh --no-api        # static only — apps use their embedded preview data
```

It serves on **one origin** with `/api/*` proxied to the agent — the same routing
model as production (CloudFront sends `/api/*` to the backend, everything else to
S3), so apps never need a per-environment URL switch. If the agent isn't running,
`/api/*` falls back and apps show embedded data. First time, install the agent deps:
`pip install -r unifyd/requirements.txt`.

This is the loop for visual-feel iteration — you do **not** deploy to AWS to try a
change. Deploy (below) is only for shipping.

---

## A note before you start (read this)

These apps include a CRM and a master-data console — proprietary JV IP, possibly
real data. **Do not put them on a naked public S3 website.** The setup below keeps
the S3 bucket *private* and serves it through CloudFront, and adds an optional
shared-password gate. It's a few more steps than "public bucket" but it's the
difference between "our IP is one guessed URL away" and "it isn't."

You'll run the AWS steps yourself (I can't reach your AWS account). Everything is
copy-pasteable. Replace `hoodie-suite` and the region with your own.

---

## 1 · Put this in a GitHub repo

```bash
cd hoodie-suite
git init
git add .
git commit -m "Initial commit: Hoodie Suite"
# create an EMPTY repo named hoodie-suite on github.com first, then:
git remote add origin https://github.com/<you>/hoodie-suite.git
git branch -M main
git push -u origin main
```

Keep the repo **private**.

---

## 2 · Create a private S3 bucket

**One-command path** (does steps 2 + 3 + first deploy — private bucket, OAC,
CloudFront distribution, bucket policy, and an initial sync; idempotent, safe to
re-run). Needs `aws configure` done first:

```bash
S3_BUCKET=hoodie-suite AWS_REGION=us-east-1 ./scripts/aws-bootstrap.sh
```

It finishes by printing the live URL and the exact GitHub secrets to set for
auto-deploy. Prefer to do it by hand / understand each piece? The manual steps
follow.

**Manual path:**

```bash
aws s3api create-bucket \
  --bucket hoodie-suite \
  --region us-east-1
# (for regions other than us-east-1, add:
#  --create-bucket-configuration LocationConstraint=<region> )

# block all public access — we serve through CloudFront, not directly
aws s3api put-public-access-block \
  --bucket hoodie-suite \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
```

Do a first upload so there's something to serve:

```bash
S3_BUCKET=hoodie-suite ./deploy.sh
```

---

## 3 · Put CloudFront in front (HTTPS + keeps S3 private)

The clean way is an **Origin Access Control (OAC)** so only CloudFront can read the
bucket. Easiest path through the **AWS Console**:

1. CloudFront → **Create distribution**.
2. Origin domain: pick your `hoodie-suite` S3 bucket.
3. Origin access: **Origin access control settings (recommended)** → create a new
   OAC → save.
4. Default root object: `index.html`.
5. Viewer protocol policy: **Redirect HTTP to HTTPS**.
6. Create. CloudFront will show a banner with a **bucket policy to copy** — paste it
   into S3 → your bucket → Permissions → Bucket policy. (This grants read access to
   *this distribution only*; the bucket stays private.)

When it finishes deploying (~5 min) you'll have a URL like
`https://d1234abcd.cloudfront.net` — that's your suite. Note the **Distribution ID**.

---

## 4 · (Recommended) Gate it with a password

Until you have real user accounts, add the shared-password gate so the apps aren't
openly browsable:

1. Edit `cloudfront/basic-auth.js` — replace the token. Generate yours:
   ```bash
   echo -n 'hoodie:your-real-password' | base64
   ```
2. CloudFront → **Functions** → Create function → paste the file's contents →
   **Publish**.
3. Your distribution → **Behaviors** → default behavior → **Edit** → under
   *Function associations*, Viewer request → your function → Save.

Now the suite asks for the password before serving anything. (This is a soft gate,
fine for a small internal/early-customer audience. When you have real users, swap it
for Cognito or an SSO in front — same distribution.)

---

## 5 · Wire up auto-deploy from GitHub

So every `git push` ships the suite.

**a.** Create an IAM user (or role) with permission to write the bucket and
invalidate CloudFront. Minimal policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow",
      "Action": ["s3:PutObject","s3:DeleteObject","s3:ListBucket"],
      "Resource": ["arn:aws:s3:::hoodie-suite","arn:aws:s3:::hoodie-suite/*"] },
    { "Effect": "Allow",
      "Action": ["cloudfront:CreateInvalidation"],
      "Resource": "*" }
  ]
}
```

Create an access key for that user.

**b.** In GitHub → your repo → **Settings → Secrets and variables → Actions** → add:

| Secret | Value |
|---|---|
| `AWS_ACCESS_KEY_ID` | from the IAM user |
| `AWS_SECRET_ACCESS_KEY` | from the IAM user |
| `AWS_REGION` | e.g. `us-east-1` |
| `S3_BUCKET` | `hoodie-suite` |
| `CLOUDFRONT_DISTRIBUTION_ID` | from step 3 |

**c.** Push anything to `main`. Watch the **Actions** tab — it syncs to S3 and
invalidates CloudFront. Done.

> Upgrade later: swap access keys for **GitHub OIDC** (a role GitHub assumes, no
> long-lived keys). It's the better practice once you're past first-light.

---

## 6 · (Optional) Your own domain

1. AWS Certificate Manager (**in us-east-1**, required for CloudFront) → request a
   cert for `suite.yourdomain.com` → validate via DNS.
2. CloudFront distribution → add that as an **Alternate domain name**, attach the
   cert.
3. Your DNS → CNAME `suite.yourdomain.com` → the CloudFront domain.

---

## The backend on-ramp

This repo is already the start of your backend, even though it only serves static
files today. The `unifyd/` engine is the first piece of the owned layer — today it
runs locally (`python unifyd/server.py`) and emits `datasets.js` the apps embed.
Here's the shape it grows into:

- **The repo is the source of truth and the deploy spine.** Front-end ships on push.
  The `unifyd/` engine lives in the same repo and grows its own deploy step.

- **Add `/api/*` without a second domain.** Run the `unifyd/` agent
  (`/api/health`, `/api/datasets`, `/api/runs`, `/api/run`, `/api/hierarchy`) as a **container**
  (Amazon ECS Express Mode — chosen over Lambda to run `server.py` as-is, no
  rewrite), then add a **second CloudFront behavior**: path pattern `/api/*` → the
  container origin; everything else → S3. One domain, front *and* back, one TLS
  cert, one auth gate. The engine's MDM console (`unifyd/hoodie_mdm.html`) already speaks this contract.

- **Secrets and connection strings** go in **AWS SSM Parameter Store** or **Secrets
  Manager**, never in the repo. The container reads them at runtime.

- **Where this maps to the architecture:** the static apps in `apps/` are *render
  targets*. `unifyd/` is where the *owned layer* lives — the scrapers, the pipeline,
  the canonical item/outlet/party model. The apps stay dumb and swappable; the value
  compounds behind `/api/*`.

Suggested first backend slice: stand up the `unifyd/` agent's COLA + Florida pulls
behind `/api/*` so one app (the MDM console) reads live data instead of an embedded
`datasets.js`. That one wire proves the whole front-to-back path.

### Stand it up (the runbook)

The agent ships as a container (`unifyd/Dockerfile`) deployed on **Amazon ECS Express
Mode** (App Runner's successor — App Runner is closed to new accounts). GitHub Actions
builds the image and Express Mode runs it; then `/api/*` points at it. The MDM console
goes live with **no front-end change** (it already fetches `/api/*`, embedded fallback).

**1 — Provision ECR + the IAM roles** (one command):

   ```bash
   AWS_REGION=us-east-1 ./scripts/provision-ecs-express.sh
   ```

   It creates the ECR repo + the two roles Express Mode needs and prints the GitHub
   **Variables** to set (`ECS_EXEC_ROLE_ARN`, `ECS_INFRA_ROLE_ARN`, `ECR_REPOSITORY`,
   `ECS_SERVICE_NAME`, `AWS_REGION`) alongside the `AWS_ACCESS_KEY_ID/SECRET` secrets.

**2 — Set those Variables/secrets, then push** (or run the **Deploy Unifyd API** workflow
manually). `.github/workflows/deploy-api.yml` builds `unifyd/Dockerfile`, pushes to ECR,
and the official `amazon-ecs-deploy-express-service` action **creates the service on the
first run and updates it on every push**. The run log prints the URL:
`https://hoodie-unifyd.ecs.<region>.on.aws/` — check `/api/health` there.

**3 — Point `/api/*` at it** (one command — backs up the distribution config first):

   ```bash
   API_ORIGIN_DOMAIN=hoodie-unifyd.ecs.us-east-1.on.aws \
   S3_BUCKET=hoodie-suite-<unique> ./scripts/add-api-cloudfront-behavior.sh
   ```

   After it propagates (~5 min), `https://<your-domain>/api/health` answers and the MDM
   console flips from "preview" to live. Verify: `curl -s https://<your-domain>/api/health`.

**4 — Make state durable** (so pulled data survives redeploys). On a container, local
disk is ephemeral, so back the agent's state with S3:

   ```bash
   STATE_BUCKET=hoodie-suite-state ./scripts/provision-state.sh
   ```

   Then set `STATE_BUCKET` (and a task role with S3 access) on the service — uncomment the
   `task-role-arn` + `environment-variables` lines in `.github/workflows/deploy-api.yml`
   and add the matching repo variables. The IAM policy to attach is printed by the script.
   Without this the agent still runs — it just resets pulled data on each redeploy. Details
   + the single-worker / single-instance notes are in `unifyd/README.md` → "State persistence".
