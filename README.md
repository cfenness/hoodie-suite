# Hoodie Suite

One front door for every Hoodie / Prism / Unifyd app. `index.html` is a launcher
shell with a persistent sidebar; each app lives in `apps/` and opens in-frame (with
an "Open full ↗" escape hatch). Pushing to `main` deploys the whole thing to S3.

```
.
├── index.html              # the suite shell (launcher)
├── apps/
│   ├── dashboard.html      # Hoodie Intelligence (Prism)
│   ├── crm.html            # Hoodie Relations (CRM)
│   ├── ttb-ingestion.html  # TTB COLA ingestion (Unifyd)
│   ├── item-mdm.html       # Item Master / MDM console (Unifyd)
│   ├── roadmap.html        # Product roadmap
│   └── principles-hub.html # Principles & Architecture KB
├── cloudfront/basic-auth.js  # optional shared-password gate
├── .github/workflows/deploy.yml  # CI: push to main → deploy
├── deploy.sh               # manual deploy from your laptop
└── README.md
```

To add an app later: drop the file in `apps/`, then add one line to the `APPS`
array near the top of `index.html`. That's the whole integration.

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
files today. Here's the shape it grows into — and it lines up with the engine
thinking:

- **The repo is the source of truth and the deploy spine.** Front-end ships on push.
  A backend service lives in the same repo (e.g. an `api/` folder) and gets its own
  deploy step in the same Actions workflow.

- **Add `/api/*` without a second domain.** Stand up the data layer as **API Gateway
  + Lambda** (serverless — cheap, scales to zero, fits a static-front world) or a
  small container service. Then add a **second CloudFront behavior**: path pattern
  `/api/*` → the API origin; everything else → S3. One domain, front *and* back,
  one TLS cert, one auth gate.

- **Secrets and connection strings** go in **AWS SSM Parameter Store** or **Secrets
  Manager**, never in the repo. Lambda reads them at runtime.

- **Where this maps to the architecture:** the static apps in `apps/` are *render
  targets*. The backend is where the *owned layer* lives — the declarative spec, the
  gates, the data. The apps stay dumb and swappable; the value compounds behind
  `/api/*`. Standing up that first endpoint (even just `/api/health`) is the moment
  the suite stops being a folder of pages and becomes a product with a spine.

Suggested first backend slice: a single read endpoint over the TTB/COLA catalog or
the MDM items, so one app (say the dashboard or the MDM console) pulls live data
through `/api/*` instead of carrying it inline. That one wire proves the whole
front-to-back path before you build anything heavier on it.
