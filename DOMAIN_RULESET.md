# Domain ruleset — the external-knowledge hook

The DQ engine (`dq.js`) and its frontier modules (`dq_frontier.js`) verify everything they can
**from the bytes of the file alone**. They deliberately do **not** know world facts — what cities
host which year's tournament, which SKUs your catalog should contain, what a source's sentinel
values are. That knowledge lives outside the engine, with you.

This file documents the **hook** where that knowledge plugs in, so a domain expert can supply it
**without touching engine code**. Until a ruleset is loaded, `claimsCoherence` emits the mandatory
disclaimer:

> No domain ruleset loaded — external plausibility (does this data match what it claims to be?) was NOT verified.

That sentence is the feature: the engine is honest about what it did *not* check.

## How to supply a ruleset

Pass a plain object as `opts.ruleset` to `DQF.claimsCoherence(header, rows, { ruleset })`
(or wire it into the UI's data-quality call). No field is required; supply only what you know.
A finding raised from a ruleset is tagged **DETERMINISTIC** — the rule and the violating value are
inspectable — but its message always credits the ruleset as the source of truth.

```js
const ruleset = {
  // Free-text label for attribution in findings ("…per <source>").
  source: "FIFA World Cup official schedule",

  // (a) Valid WHERE values for each WHEN value. Keys are the exact period values as they appear
  //     in the data; values are the allowed location strings. A row whose (period, location)
  //     pair is not allowed is flagged. This catches the "file says 2022 but lists another
  //     year's host cities" contradiction the engine cannot detect on its own.
  locationsByPeriod: {
    "2022": ["Doha", "Lusail", "Al Khor", "Al Rayyan", "Al Wakrah"],
    "2026": ["Inglewood", "Arlington", "Kansas City", "Atlanta", "Toronto", "Mexico City"]
  },

  // (b) Expected entity roster — either a flat array (valid for all periods) or keyed by period.
  //     Used to flag entities present that shouldn't be, or (future) expected-but-absent.
  expectedEntities: ["Spain", "France", "Brazil", "Argentina"],
  // expectedEntities: { "2026": ["USA", "Canada", "Mexico", ...] },

  // (c) Known sentinel / placeholder values per column (e.g. -1, 9999, "N/A", "TBD") that a
  //     source emits and that should not be treated as real data.
  sentinels: {
    "attendance": [-1, 0, 9999],
    "stadium": ["TBD", "Unknown"]
  }
};
```

## Interface contract

- **Input:** the ruleset object above + the parsed `header` / `rows`. The engine resolves the
  WHEN column (date/year/season/period name) and WHERE column (city/venue/location name)
  heuristically; you can also pin them via `opts.when` / `opts.where` (future).
- **Output:** findings appended to the `claimsCoherence` result, each `DETERMINISTIC`, severity by
  kind: `claims.ruleset_location` (high), `claims.ruleset_sentinel` (medium).
- **Absence:** no ruleset → exactly one `claims.no_ruleset` finding carrying the mandatory sentence
  above. Loading a ruleset suppresses that disclaimer.

## Anti-goal

Do not encode guesses here to make the engine *look* smarter. A ruleset is asserted knowledge a
human stands behind. If you don't have it, the honest "not verified" disclaimer is the correct
output — a confident wrong claim about what the data is would be worse.
