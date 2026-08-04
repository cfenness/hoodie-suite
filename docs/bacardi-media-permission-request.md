# Permission request — Bacardi Media Centre

**Status: DRAFT for review. Not sent.** Fill the bracketed fields, decide the ask (see *Choosing the
scope*), then send. Nothing in the pipeline changes until a reply is recorded — see *If they say yes*.

---

## Why this letter exists

Bacardi's Terms & Conditions (last updated March 2023) govern `media.bacardilimited.com` by their own
§1, which extends them to "any and all other online or digital platforms … which we maintain". They
grant no reuse licence: §3 states that use of the Site "does not grant you any rights, title, interest
or license to any Materials", permits downloads only for "your lawful, personal, non-commercial use",
and adds "You must not use any part of the Materials on our Site for commercial purposes unless
expressly permitted by us". §4 adds "You are not permitted to use the Materials outside of the Site."

§13 names the route for exactly this situation, verbatim:

> "If you wish to make any use of Material on our Site other than that set out above, please address
> your request for the attention of our Digital Director as specified in section 20 of the present
> Terms and Conditions of Website Use."

§20 identifies "us" as **Bacardi-Martini B.V., registered in Weena 505, 3013 AL, Rotterdam, The
Netherlands**.

So this request is addressed by role, to the address their own terms specify. No Digital Director is
named publicly; addressing the role is what §13 asks for.

## Delivery routes (verified 2026-08-03)

| Route | Notes |
|---|---|
| `https://contact.bacardilimited.com/` | The contact route published on bacardilimited.com. Likely the fastest path; may require pasting the letter into a form. |
| `bacardilimited.com/media/` → "Get in touch" | The media section's own contact anchor — arguably the most on-topic recipient. |
| Post: Bacardi-Martini B.V., Weena 505, 3013 AL, Rotterdam, Netherlands | The §20 registered address. Slow, but it is the address §13 points at, which matters if the request ever needs to be shown to have been properly made. |

Sending by **both** a web route and post is worth the ten minutes: it creates a dated record.

---

## Choosing the scope

The letter below asks for **internal reference use only** — the narrowest thing that unlocks real
work, and the easiest for a brand-protection team to approve. Two other options, if you want to aim
higher:

- **Editorial/press display** — lets Bacardi imagery appear in Hoodie surfaces shown to customers,
  with attribution. A bigger ask; brand teams often already have a press-use policy that covers it,
  so it is worth *mentioning* even if the narrow ask is the priority.
- **Commercial redistribution** — imagery inside a product you sell. Do not lead with this. It needs
  a negotiated licence, not a permission letter.

The draft asks for the first and flags the second as an open question. Delete the flagged paragraph
if you would rather keep the ask absolutely minimal.

---

## The letter

> **Subject:** Request for permission to use Bacardi Media Centre imagery — Terms & Conditions §13

For the attention of the **Digital Director**
Bacardi-Martini B.V.
Weena 505, 3013 AL, Rotterdam, The Netherlands

Dear Digital Director,

I am writing under §13 of your Terms and Conditions of Website Use, which asks that any request to
use Material from your sites beyond the permitted personal, non-commercial use be addressed to you.

**Who we are.** [COMPANY LEGAL NAME] operates Hoodie, a data platform for the beverage-alcohol trade.
We build reference data about products — brand, product, pack size, identifiers — and market
intelligence for suppliers, distributors and retailers. [ONE LINE ON CUSTOMERS OR STAGE, e.g. "We
work with distributors and brand owners across the US market."]

**What we have done so far, and deliberately not done.** We have catalogued the public Bacardi Media
Centre drive ("Bacardi Public") at a metadata level only: filenames, folder paths, file types, sizes
and timestamps — 2,490 assets. We have **not** downloaded, stored, copied, or processed any image or
video asset from the drive, because we read your terms as not permitting it. Our pipeline is built so
that this is enforced in software rather than left to good intentions: each source carries a machine-
readable record of its terms, and our systems are technically prevented from retrieving or processing
an asset from a source whose terms do not cover that use. Bacardi is currently configured to permit
nothing beyond metadata. We also honour your robots.txt and read only the paths it allows.

We mention this because we would rather ask first than explain afterwards.

**What we are asking for.** Permission to download and store official product imagery from the public
Bacardi Public drive, for **internal reference use only** — specifically, to serve as reference images
that help our systems recognise Bacardi products in retail photography (shelf images, menus, product
listings). This is a recognition aid used inside our own systems.

Concretely, we are asking to:

1. retrieve official product images (bottle and pack shots) from the public drive;
2. store them, and computed representations of them, in our internal systems;
3. use them solely to identify and match Bacardi products in third-party imagery.

**What we would commit to, and are happy to see written into any permission you grant:**

- **No redistribution.** The imagery would not be published, resold, licensed onward, or displayed to
  our customers or any third party.
- **No alteration** beyond routine technical processing (resizing, format conversion) required to use
  an image as a reference.
- **Attribution** wherever the imagery is visible to anyone, in whatever form you prefer.
- **Revocable at will.** On request from you, we will stop using the imagery and delete it and
  anything derived from it, and confirm in writing when that is done.
- **Scope-limited to the public drive.** We would not seek access to gated or non-public drives.
- **Polite retrieval.** A single pass at a low request rate, from an identified user-agent, honouring
  robots.txt. We would not re-download unchanged assets.

**One further question, if it is easy to answer.** If Bacardi already has a standing press or
editorial-use policy for this imagery, we would be glad to know its terms — some of what we do would
sit naturally within a press-use grant, and it may be simpler for you to point us at an existing
policy than to write a bespoke permission. [DELETE THIS PARAGRAPH IF YOU WANT THE NARROWEST ASK.]

If any of the above is not something you can grant, we would welcome a narrower permission, or a
clear no — either is genuinely useful, and we will configure our systems to match your answer.

I am happy to sign a licence or usage agreement of your drafting, or to answer any technical
questions about how the imagery would be handled.

With thanks for your time,

[NAME]
[TITLE]
[COMPANY LEGAL NAME]
[EMAIL] · [PHONE]
[POSTAL ADDRESS]

---

## If they say yes

Do not loosen anything by hand. The permission becomes a new revision of
`unifyd/rights_records/dam-bacardi.json`:

1. Add the reply — verbatim — alongside the ToS snapshot, with the date and who sent it.
2. Set `permissions.image_use` and `permissions.scope` to match **what they actually granted**, not
   what was asked. An internal-reference grant is `scope: "internal_only"`, not `editorial_press`.
3. Set `counsel_cleared: true` only once someone has read the reply and accepted it. Until that flag
   is set the grant is inert by design — `rights.may()` denies every asset action on an uncleared
   grant, so nothing can start flowing as a side effect of editing the record.
4. Carry any conditions across: `attribution_required`, `alteration_allowed`, `expiry`.
5. Re-run `python3 unifyd/dam_rights_test.py`, then `python3 unifyd/dam_gallery.py dam-bacardi` — the
   gallery will begin deriving on the next run, and the rows will cite the new record revision.

## If they say no, or do not reply

Nothing needs doing. The record already reads `prohibited / none`, the pipeline already derives
nothing, and the facts feed (`brand_events`) is unaffected either way — launch dates, markets and
price points are uncopyrightable and continue to flow. A refusal costs us the CV gallery for this
supplier and nothing else.
