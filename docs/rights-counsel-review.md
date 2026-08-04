# Rights model — counsel review packet

**Schema version 1.0. Not yet signed off.**

*Nine open questions at §6. Q5 (are hashes/embeddings derivative works?) and Q9 (does a trade grant reach us?) are the two that change what the system can do.*

This is the document a lawyer reads to accept or reject the permission model we use when harvesting
third-party media libraries. It is self-contained: you should not need to read any code. Where a rule
is enforced in software, the file is named so it can be verified.

The ask is narrow. We are not asking counsel to approve a business plan. We are asking three things:

1. Is the **taxonomy** (below, §2) the right set of distinctions?
2. Are the **five decision rules** (§3) ones you would defend?
3. Which of the **nine open questions** (§6) do we have wrong?

---

## 1. What the system does, in one paragraph

We harvest suppliers' public media centres. Two kinds of thing come out. **Facts** — a product launch
date, the market it launched in, a stated retail price — which we treat as uncopyrightable and always
keep. **Assets** — studio photography and video owned by the supplier — which we treat as licensed
only to the extent the supplier's terms say so, and otherwise do not retain, copy, process, or
display. Every source carries a machine-readable record of its terms; the software is structurally
unable to act on an asset outside what that record permits, and every attempt — allowed or refused —
is logged.

**Current live position:** one supplier is harvested (Bacardi). Their terms grant no reuse licence, so
we hold **2,490 asset pointers and zero bytes**. No image has been downloaded, stored, hashed, or
embedded from any supplier. The image side of this capability has never emitted anything.

## 2. The taxonomy

Two independent axes per source.

**`image_use`** — what the terms say about reusing assets:

| Value | Meaning |
|---|---|
| `permitted` | The terms affirmatively grant reuse to the reader. |
| `prohibited` | The terms forbid it, or grant only personal/non-commercial use. |
| `silent` | The terms do not address reuse at all. |

**`scope`** — how far a grant reaches. A ladder; each level includes the ones below it:

| Value | Meaning |
|---|---|
| `none` | No asset use of any kind. |
| `internal_only` | Reference/model use inside our own systems. Never shown to a customer. |
| `editorial_press` | The above, plus display in press/editorial contexts. Not resale. |
| `commercial_redistribution` | The above, plus inclusion in a product we sell. |

Plus: `attribution_required`, `alteration_allowed`, `trade_partner_only`, `expiry`, a `confidence`
grade, and `needs_counsel`.

**`facts_use`** is carried separately and is `permitted` in every record, on the view that facts are
uncopyrightable. §6 Q1 asks whether you accept that.

## 3. The five decision rules

Each is enforced in `unifyd/rights.py` and covered by tests in `unifyd/rights_test.py`.

**R1 — Facts always flow.** Dates, markets, prices, product names and the *existence* of a file
(its name, size, type, URL) are ungated regardless of image terms. Rationale: facts are not
copyrightable subject matter, and a pointer is a reference to a work, not a copy of it.

**R2 — Silence is a hold.** `silent` behaves identically to `prohibited`. We never infer permission
from the absence of a prohibition. Rationale: copyright is reserved by default; the cost of holding is
a gap in an internal gallery, and the cost of guessing wrong is a licensing claim.

**R3 — Scope is enforced, not just the yes/no.** An `editorial_press` grant permits internal
reference use and press display and **denies** redistribution. Same asset, different doors.

**R4 — Counsel guards the affirmative act, not the hold.** A record that says "hold" and is enforced
as a hold needs no lawyer. A record asserting we *may* use someone's property is inert until a human
sets `counsel_cleared`. Editing the permission field alone changes nothing — the gate still refuses.

**R5 — Staleness is a hold.** If a source's terms change after review, every asset action is denied
until a human re-reads them. A permission parsed from terms that no longer exist is worse than none,
because it looks like diligence.

## 4. Where it is enforced

| Control | Where | What it prevents |
|---|---|---|
| Single asset-bytes chokepoint | `dam.asset_bytes()` | Any code path fetching an asset without a permission check. |
| Mechanical scan | `dam_dna_test.py` | A future connector fetching an asset URL directly, bypassing the chokepoint. |
| Derivation gate | `dam_gallery.py` | Hashes and embeddings — derivative works — being computed on assets we may not process. Checked **per asset**, not once per run. |
| Prose ceiling | `dam.land()` | Copyrightable body text riding into storage inside a "fact" column. |
| Registry ratchet | `dam_rights_test.py` | A source being added without a reviewed terms record. |
| Emission log | `dam_emissions` | The claim "we never emitted outside scope" being an assurance rather than an audit trail. |

## 5. Method limits we impose on ourselves

Public share drives, documented public APIs, and robots-permitted paths only. Specifically **not**
done: authentication bypass, credential use, subdomain enumeration, certificate-transparency mining,
parameter tampering. Two live consequences, both recorded in the data:

- Trinchero's asset portal declares itself **private** and its anonymous API token returns 403. We
  stopped. It is recorded as gated, not harvested.
- Three suppliers' corporate sites are behind **age gates**. We do not defeat them. Where the same
  URLs appear in the supplier's own published `sitemap.xml` we read those; otherwise the supplier is
  flagged for a human to look at.

## 6. Open questions — what we need decided

**Q1 — Is the facts/assets line drawn in the right place?** We treat a launch date, a market, a stated
price and an ABV as uncopyrightable facts, freely extractable from a press release. Correct?

**Q2 — Is reading a press release to extract facts defensible?** To get a launch date we fetch the
supplier's PDF/DOCX press release, extract the facts, and discard the file. The bytes are never
stored, the prose never lands (enforced by a 500-character ceiling on any stored field), and only the
extracted facts persist. This is the one place we retrieve a copyrighted file from a source whose
terms forbid reuse. **We think this is fair; we would like you to confirm it.**

**Q3 — Are asset POINTERS unproblematic?** For a prohibiting source we store the asset's URL,
filename, type, size and timestamps — but no bytes. Is a catalogue of URLs a use that any of these
terms restricts?

**Q4 — Does "personal, non-commercial use" ever cover us?** Several suppliers permit downloads for
"lawful, personal, non-commercial use". We read that as excluding a business, so we treat it as
`prohibited`. Is that the right reading, or is internal reference use inside a company arguably
within it?

**Q5 — Are perceptual hashes and CV embeddings derivative works?** We assume yes and gate them at the
same level as storing the image. If they are not — if a hash is closer to a fact about a file — a
whole category of use opens up under sources that currently hold. This is the single highest-value
question in this document.

**Q6 — Is `internal_only` the right floor?** Our narrowest grant level still means storing a copy on
our systems indefinitely. Should there be a narrower level — process-and-discard, retaining only the
derived representation?

**Q7 — Does an editorial/press grant reach us at all?** Press grants are typically addressed to
"accredited media" or "journalists". We are neither. Should a press grant be treated as not applying
to us unless it says otherwise?

**Q9 — Does a TRADE grant reach us, and what makes us a trade partner?** A distributor or
syndication platform publishes assets precisely so the trade can sell the product — "authorized
retailers and distributors may use these images in connection with the sale of our products". That is
a real licence, but it is addressed to a **class**, and it is not obvious we are in it. We treat such
a grant as conditioned: it is denied until someone records `trade_partner_verified` establishing that
we qualify. Two sub-questions: (a) what would actually establish it — a distribution agreement, a
licence, a customer relationship with the supplier? (b) if our *customer* is an authorized retailer,
does serving them the supplier's imagery fall inside the grant made to them?

**Q8 — Is the schema versioning adequate for a buyer's diligence?** Each record states the schema
version it was authored under and carries a `schema_signoff` field. A future model change shows up as
a version skew across every record rather than silently reinterpreting them. Is that the shape a
buyer's counsel would want?

## 7. What a false positive looked like, and why we mention it

Our terms parser initially classified three suppliers as *granting* royalty-free editorial use at high
confidence. All three were the same misread: the clause it found was the **user-generated-content**
licence — the licence *you* grant *them* when you upload something to their site. It runs inbound, not
outbound.

Acting on it would have built an image gallery on assets nobody had licensed to us. The parser is now
direction-guarded and those suppliers correctly read as holds. We raise it unprompted because it is
the failure mode most likely to recur, and because the safeguards in §4 are the reason it was caught
before anything was retrieved rather than after.

## 8. Recording the decision

Sign-off is recorded per source, not asserted in code:

- **Schema accepted** → set `schema_signoff` on each record (reviewer, date, schema version).
- **A specific grant accepted** → set `counsel_cleared: true` on that record. Until then the grant is
  inert and the software refuses every asset action, whatever the permission field says.
- `python3 unifyd/rights.py --queue` lists everything still awaiting a decision, and distinguishes
  items that **cost capability** (a grant we are holding) from items that do not (a hold that is
  correctly held).

Current queue: **1 item — schema sign-off. Nothing is costing capability**, because no supplier has
granted anything yet.
