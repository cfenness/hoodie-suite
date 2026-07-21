# Product & commercial guide — surfaces, positioning, the standard

## What the product is

A master-data + analytics platform for beverage alcohol (first domain), built on a posture the
incumbents can't copy: **data fiduciary**. Resolve identity once, from public first principles,
with evidence behind every value — then everything downstream (analytics, the field app, customer
feeds) reads the same clean spine. "Don't change the label": stewardship over performance
creativity, zero restatements as the track record.

## The surfaces (what to demo, what each does)

- **MDM console** (suite → #mdm): the master-data control plane. The **Flow** tab is the
  master-building workbench — seed a master from landed sources, watch bad data scream in the
  profile, resolve conflicts by RULE, click any golden record for its provenance ("here are the
  rows it came from"). This is the trust story, live.
- **Tickets** (#tickets) & **Roadmap** (#roadmap): how work is planned, sized, verified — the
  operating discipline is itself demoable.
- **Hoodie App** (mobile): the field surface — Prism (the book cut by any dimension), Hoodie
  Intelligence (KPIs + auto-brief), Accounts (outlet leaderboard). All read the same canonical book.
- **Serve API**: customers join on stable **Hoodie IDs**; the serve layer is real-only by
  construction — synthetic data physically cannot reach a consumer.

## The standard (say it precisely)

**90–95% automatch, accuracy-first.** It is NOT a quota: accuracy is the binding constraint and
automation is maximized subject to it — 90–95% is the high end of the range where accuracy stays
front and center. The rate is earned through better verification tiers (never by loosening match
thresholds), and it's measured as a pair: automatch shares AND false-merge precision by audit
sampling. The 5–10% a human still reviews is the design working, not failing.

## Why we win (the differentiators)

1. **AI-native verification** — agents that fetch the verifiable fact (the brand page, the TTB
   label, "is this store open") instead of guessing from string similarity. Incumbents structurally
   can't retrofit this.
2. **Open & portable** — Parquet + SQL, no lock-in; a customer can always walk with their data.
3. **Domain-general** — bev-alc is config, not code; "syndicate the lettuce industry" is a config
   pack, not a rebuild.
4. **Evidence everywhere** — provenance on every value, honest degradation, real≠synthetic
   discipline. Trust is the product.

## Vocabulary (so we all say the same thing)

- **Golden record** — the one resolved record per real-world thing, derived (never hand-edited).
- **Hoodie ID** — our stable identifier for a golden record; the customer's join key.
- **Survivorship** — the rule choosing which source's value wins a field.
- **Provenance** — which source supplied each value, and why it won.
- **Automatch** — a match decision made without a human, with evidence.
- **Steward** — the human working the residue: conflicts, matches, verifications.

## Where the deep material lives

Positioning & deal context: the CRM (#crm) and sales tutorial. Roadmap of record: #roadmap.
Engineering truth: this handbook's other pages + `MDM_FLOW.md`.
