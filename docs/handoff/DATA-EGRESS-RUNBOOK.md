# Warehouse egress → Google Drive (runbook)

Archive/handoff copy of the Tigris warehouse into a Google Drive folder. It is a **copy job,
not a database export** — the data is already Parquet in object storage, and it stays Parquet.
Drive is not a query engine; the analytical home remains Parquet-in-object-store / Snowflake.

Tool: `tools/warehouse_egress.py`. **Read-only against Tigris** — the inventory reads Parquet
footers and manifests, the transfer is one-way `rclone copy`, and there is no code path that
calls `warehouse.write_*`.

## Fastest path — run it on Fly (recommended)

Fly already has the Tigris creds in the app env and sits next to the bucket, so the transfer is
Tigris → Drive and **the bytes never cross a laptop**. Two one-time steps, then one command.

**1. Get a Drive token (once, on any machine with a browser):**

```bash
rclone authorize "drive"
```

Sign in with the **Hoodie** Google account. It prints a JSON token — that's the whole credential.

**2. Run the job:**

```bash
flyctl machine run --rm <image> -a hoodie-suite --vm-memory 2048 \
  -e GDRIVE_TOKEN='<the JSON from step 1>' \
  --command "bash /app/unifyd/run_egress.sh all --reference-csv"
```

Get `<image>` from `flyctl image show -a hoodie-suite`. The machine installs rclone, inventories,
copies, verifies, and self-destroys. Watch it with `flyctl logs -a hoodie-suite`.

The job **prints the destination Google account before it transfers anything** — check that line
says the Hoodie account, not a personal one. `whoami` checks it on its own:

```bash
python3 tools/warehouse_egress.py whoami
```

If you'd rather not put the token on a command line, set it as a secret instead
(`flyctl secrets set GDRIVE_TOKEN='…' -a hoodie-suite`) and drop the `-e` flag.

## Where this can run

It needs the Tigris credential trio **and** network reach to `fly.storage.tigris.dev` — the Fly
machine, or a local shell with `warehouse.env` sourced. It does **not** run in a Claude Code web
session: those containers get placeholder `AWS_*` values, and the egress policy 403s
`fly.storage.tigris.dev`, `api.fly.io` and `rclone.org` alike.

---

## Running it locally instead

Slower — the bytes go Tigris → your machine → Drive — but it needs no Fly access.

### 0 — preflight

```bash
set -a; source warehouse.env; set +a     # gitignored; lives on your machine, not in the repo

python3 tools/warehouse_egress.py preflight
```

Preflight fails **loudly and specifically** on any missing variable and refuses to fall back to
warehouse local-disk mode — a silent local fallback would archive an empty dev directory and
report success. It also rejects placeholder values (e.g. a literal `proxy-injected`) rather than
letting them surface as a confusing auth error three steps later.

#### rclone + the Drive remote

```bash
brew install rclone                              # macOS
curl https://rclone.org/install.sh | sudo bash   # linux

rclone config
#  n) New remote   name> gdrive   Storage> drive
#  client_id / client_secret> (blank is fine)
#  scope> 1 (full access)      advanced> n      auto-authenticate> y
```

The browser step signs you in — **use the Hoodie Google account, not a personal one.** Confirm
which account the token actually belongs to before copying anything:

```bash
rclone about gdrive:
rclone config userinfo gdrive:     # prints the email the remote is bound to
```

The Tigris side needs no `rclone config` entry: the tool injects it via `RCLONE_CONFIG_TIGRIS_*`
env vars at call time, so no credential is written to `rclone.conf` or left on disk.

### 1 — inventory

```bash
python3 tools/warehouse_egress.py inventory
```

Writes `docs/handoff/DATA-EGRESS-MANIFEST.md`: every dataset with row count, byte size, object
count, partition style, and physical prefix, plus the large tables called out separately.

Three layouts coexist and the archive carries all of them, plus `_manifest/`:

| layout | path |
|---|---|
| v1 flat | `<prefix>/<name>.parquet` |
| v2 bucketed | `<prefix>/<name>/__b=<hex>/part-v<n>.parquet` + `<prefix>/_manifest/<name>.json` |
| date-partitioned | `<prefix>/<name>/*.parquet` |

**`_manifest/` is not optional.** A v2 bucketed table cannot be resolved back to its live part
files without its manifest JSON, so a parquet-only copy restores as an unreadable archive.
Copying the whole `<prefix>/` tree (what the tool does) covers this by construction.

Note that v1 tables live *inside* `<prefix>/`, not at the bucket root — `WAREHOUSE_PREFIX`
defaults to `warehouse`. The inventory still sweeps the bucket root and reports anything found
outside the prefix, since a table written while the prefix was blank would otherwise be missed.

### 2 — copy

```bash
python3 tools/warehouse_egress.py copy [--include-raw-payloads] [--reference-csv]
```

Runs, in effect:

```bash
rclone copy tigris:$BUCKET/warehouse "gdrive:Hoodie/warehouse" \
  --transfers 8 --checkers 16 --fast-list --progress
```

- **`raw_payloads` is excluded by default** — append-only raw JSON, scraper exhaust rather than
  master data, and it can carry unfiltered response bodies. `--include-raw-payloads` opts in.
- `--reference-csv` additionally emits CSV copies of the small human-facing reference tables
  (`cpi_reference`, `cex_reference`, `tax_rates`, `census_*`) to `gdrive:Hoodie/reference-csv/`
  via DuckDB `COPY`. Those are the ones people open in Sheets. Nothing else is converted.
- Not `--immutable`: the warehouse is live, and a scraper rewriting a part mid-copy would abort
  the run. `copy` is one-way and never mutates the source.
- Resumable — `rclone copy` skips what already matched, so a dropped run is just re-run.
- A per-table failure is collected and listed at the end, not fatal; one unreadable table must
  not abandon the other N.

### 3 — verify

```bash
python3 tools/warehouse_egress.py verify
```

Compares object count and total bytes, source vs dest (`rclone size --json` on both). A mismatch
is **reported, never smoothed over** — a deliberate `--exclude` such as `raw_payloads` explains a
gap; anything else does not. Appends a `verified: <date>, <n tables>, <total GB>, source-vs-dest`
line to the manifest.

Get the shareable folder link with:

```bash
rclone link gdrive:Hoodie/warehouse
```

### All at once

```bash
python3 tools/warehouse_egress.py all --reference-csv
```
