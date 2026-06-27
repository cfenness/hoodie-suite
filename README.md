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
│   ├── estate-map.html        # Data & model layer map
│   ├── ttb-ingestion.html     # TTB COLA ingestion view (Unifyd)
│   ├── item-mdm.html          # Item Master / MDM console (Unifyd)
│   ├── training-suite.html    # The Bench — five training rooms
│   ├── sales-tutorial.html    # The Long Game — sales room
│   ├── roadmap.html           # Product roadmap
│   ├── principles-hub.html    # Principles & Architecture KB
│   ├── spine-adapter.html     # Reference: how apps join the spine
│   ├── tasting-room.html
│   └── perceptual-science-tutorial.html
├── spine/
│   ├── spine.js               # the shared backbone (host + connect)
│   └── hierarchy.sample.json  # canonical hierarchy (sample)
├── unifyd/                    # the ingestion ENGINE — scrapers + local agent (NOT deployed)
│   ├── server.py              #   local agent (Flask) — serves hoodie_mdm.html + runs real pulls
│   ├── ttb_cola_scraper.py    #   TTB COLA registry scraper
│   ├── pull_sources.py        #   batch puller (Florida + COLA)
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
  (`/api/health`, `/api/datasets`, `/api/runs`, `/api/run`) as a **container**
  (App Runner or Lightsail — chosen over Lambda to run `server.py` as-is, no
  rewrite), then add a **second CloudFront behavior**: path pattern `/api/*` → the
  container origin; everything else → S3. One domain, front *and* back, one TLS
  cert, one auth gate. `apps/mdm.html` already speaks this contract.

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

The container artifacts live in `unifyd/` (`Dockerfile`, `apprunner.yaml`). Two
one-time setup steps, then it auto-deploys on push and the MDM console goes live with
**no front-end change** (it already fetches `/api/*`, with embedded data as fallback).

**1 — Deploy the agent as a container.** Lowest-ceremony path (no Docker, no ECR):

   - App Runner console → **Create service** → Source: this GitHub repo, branch `main`,
     **Source directory `unifyd`**, deployment trigger **Automatic**. It reads
     `unifyd/apprunner.yaml`, builds, and **re-deploys on every push to `main`**.
   - Note the service URL it gives you, e.g. `xxxx.us-east-1.awsapprunner.com`, and
     check `https://<service-url>/api/health`.
   - Prefer a real image (Lightsail/ECS, or App Runner image mode)? Build `unifyd/Dockerfile`
     instead — `docker build -t hoodie-unifyd unifyd/ && docker run -p 8080:8080 hoodie-unifyd`.

**2 — Point `/api/*` at it** (one command — backs up the distribution config first):

   ```bash
   API_ORIGIN_DOMAIN=xxxx.us-east-1.awsapprunner.com \
   S3_BUCKET=hoodie-suite ./scripts/add-api-cloudfront-behavior.sh
   ```

   After it propagates (~5 min), `https://<your-domain>/api/health` answers and the MDM
   console flips from "preview" to live. Verify: `curl -s https://<your-domain>/api/health`.

> **State is ephemeral** on the container until persistence is added — pulled datasets
> reset on redeploy. That's fine to prove the wire; the next step is S3-backed state (or
> a small DB) so `agent_state/` survives. See `unifyd/README.md`.
