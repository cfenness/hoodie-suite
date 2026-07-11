"""cuisine.py — restaurant cuisine type, deterministic-first with a Claude fallback where unsure.

Cuisine predicts the alcohol profile (sushi -> sake / Japanese beer, taquería -> tequila / cerveza,
trattoria -> wine, sports bar -> beer / cocktails) and sharpens which merchants are worth pulling. DoorDash
store pages carry schema.org servesCuisine — authoritative — so a pull reads it straight from the page. For a
tile-only geo harvest (no page fetch) we fall back to a name heuristic, and ONLY where both are silent do we
batch the unknown names through Claude (opt-in on ANTHROPIC_API_KEY) — "feed it through Claude where unsure".
"""
import json, os, re, urllib.request


def from_page(html):
    """schema.org servesCuisine array on the store page — authoritative, ordered most→least specific."""
    m = re.search(r'"servesCuisine":\s*\[([^\]]*)\]', html or "")
    if not m:
        return []
    return [c.strip().strip('"') for c in m.group(1).split(",") if c.strip().strip('"')]


# DoorDash tags a store with a SET of cuisine/dish tags (["Pizza","Wings","Burgers","Steak"]) — the first
# isn't the answer ("Pizza" for a brewhouse). We DERIVE one canonical cuisine by voting over all tags: a
# national-cuisine tag is worth more than a dish format, an umbrella ("Asian") least. (regex, canonical, weight)
_TAGW = [
    (r"^japanese$|izakaya", "Japanese", 3), (r"sushi|ramen|yakitori|teriyaki|\budon\b|donburi|sake\b", "Japanese", 2),
    (r"^mexican$|tex-?mex", "Mexican", 3), (r"taco|burrito|taquer|nachos|quesadilla", "Mexican", 2),
    (r"^italian$", "Italian", 3), (r"pasta|trattoria|pizzeria|calzone|\bsub(?:s| shop)", "Italian", 2), (r"pizza", "Italian", 1),
    (r"^chinese$|dim sum|szechuan|sichuan|cantonese|hunan", "Chinese", 3), (r"lo mein|fried rice|\bwok\b", "Chinese", 2),
    (r"^thai$|thai food", "Thai", 3),
    (r"^korean$|korean bbq|kbbq", "Korean", 3), (r"bibimbap|bulgogi", "Korean", 2),
    (r"^vietnamese$|\bpho\b|banh mi", "Vietnamese", 3),
    (r"^indian$|tandoori|biryani", "Indian", 3), (r"\bcurry\b|masala", "Indian", 1),
    (r"mediterranean|greek|lebanese|turkish|shawarma|falafel|kebab|gyro|\bhalal\b", "Mediterranean", 3),
    (r"caribbean|jamaican|cuban|peruvian|colombian|venezuel|puerto rican|dominican|latin american|\bjerk\b", "Caribbean/Latin", 3),
    (r"^french$|creperie|cr[eê]pe", "French", 3), (r"filipino", "Filipino", 3), (r"ethiopian|african", "Ethiopian", 3),
    (r"hawaiian|\bpoke\b", "Hawaiian", 2), (r"cajun|creole", "Cajun", 3),
    (r"\bsteak", "Steakhouse", 2), (r"seafood|oyster|\bcrab\b|lobster|raw bar", "Seafood", 2),
    (r"\bbbq\b|barbecue|smokehouse", "BBQ", 2),
    (r"sports bar|brew(?:ery|pub|house)|gastropub|beer garden|taproom|\bpub\b", "American Bar", 2), (r"wings", "American Bar", 1),
    (r"breakfast|brunch|pancake|waffle|\bdiner\b", "Breakfast", 2),
    (r"burger|hot ?dog|sandwich|\bdeli\b|fried chicken|comfort|southern|soul food", "American", 2), (r"^american", "American", 2),
    (r"fast food|snacks?", "American", 1), (r"^asian$|pan-?asian|noodle", "Asian", 1), (r"^latin$", "Latin", 1),
    (r"dessert|bakery|ice cream|\bcoffee\b|caf[eé]|smoothie|\bjuice\b|boba|bubble tea", "Dessert/Cafe", 0.5),
]


def derive(tags):
    """Vote over the full tag set -> one canonical cuisine. Ties break to the earliest (most-relevant) tag."""
    scores, firstpos = {}, {}
    for i, t in enumerate(tags):
        tl = t.lower().strip()
        for pat, canon, w in _TAGW:                              # first (most specific) mapping per tag
            if re.search(pat, tl):
                scores[canon] = scores.get(canon, 0) + w
                firstpos.setdefault(canon, i)
                break
    if not scores:
        return ""
    best = max(scores.values())
    return min((c for c, s in scores.items() if s == best), key=lambda c: firstpos[c])


# name -> cuisine (deterministic heuristic, first match wins — order matters: specific before generic)
_NAME_HINTS = [
    ("Japanese", r"sushi|ramen|izakaya|teriyaki|hibachi|yakitori|\budon\b|donburi|sake bar|tokyo|osaka|sakura|\bnori\b|\bkoi\b"),
    ("Mexican", r"taquer[ií]a|cantina|mexican|\btaco|burrito|azteca|jalisco|tijuana|el toro|los \w+|casa \w+|maya\b"),
    ("Italian", r"trattoria|pizzeria|osteria|ristorante|italian|\bpasta\b|napoli|\broma\b|toscana|little italy|mangia"),
    ("Chinese", r"chinese|szechuan|sichuan|hunan|dim sum|\bwok\b|panda|peking|canton|\bchow\b|mandarin"),
    ("Thai", r"\bthai\b|bangkok|pad thai|\bsiam\b|thai basil"),
    ("Indian", r"indian|tandoor|\bcurry house\b|masala|biryani|punjab|\bdelhi\b|mumbai|\btaj\b"),
    ("Korean", r"korean|\bkbbq\b|bulgogi|bibimbap|\bseoul\b|kimchi|\bgogi\b"),
    ("Vietnamese", r"\bpho\b|vietnam|banh mi|saigon"),
    ("American Bar", r"sports bar|\bgrill\b|\bpub\b|tavern|ale ?house|brewhouse|brew(?:ery|ing)|taproom|"
                     r"bar (?:&|and) grill|\bwings?\b|saloon|icehouse|beer garden|gastropub"),
    ("Steakhouse", r"steak ?house|chop ?house|prime rib"),
    ("Seafood", r"seafood|oyster|\bcrab\b|lobster|fish house|shrimp|raw bar|\bpoke\b"),
    ("Caribbean/Latin", r"caribbean|jamaican|\bjerk\b|cuban|havana|latin|peruvian|colombian|venezuel"),
    ("Mediterranean", r"mediterranean|greek|lebanese|falafel|shawarma|kebab|gyro|hummus"),
    ("American", r"\bdiner\b|\bburger|\bbbq\b|barbecue|smokehouse|\bkitchen\b|\bcaf[eé]\b|bistro|eatery|\bgrille\b"),
]


def from_name(name):
    n = (name or "").lower()
    for cui, pat in _NAME_HINTS:
        if re.search(pat, n):
            return cui
    return ""


def classify(name, html=None):
    """-> (primary, all_tags[], source) where source ∈ page | name | unsure. Primary is DERIVED from the
    full page tag set (not the first tag); all_tags keeps DoorDash's complete servesCuisine array."""
    if html:
        tags = from_page(html)
        if tags:
            return (derive(tags) or from_name(name) or tags[0]), tags, "page"
    nm = from_name(name)
    if nm:
        return nm, [nm], "name"
    return "", [], "unsure"


def claude_cuisine(names, model="claude-opus-4-8"):
    """Batch-infer cuisine for the uninformative names. Opt-in: no-op unless ANTHROPIC_API_KEY is set.
    One request for the whole batch -> {name: cuisine}. Returns {} on any error (deterministic path stands)."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    names = [n for n in dict.fromkeys(names) if n]
    if not key or not names:
        return {}
    prompt = ("Classify each restaurant by its single primary cuisine (1-2 words, e.g. Japanese, Mexican, "
              "Italian, Chinese, Thai, Indian, Korean, Vietnamese, American, American Bar, Steakhouse, "
              "Seafood, Caribbean/Latin, Mediterranean, Pizza, Breakfast). Judge only from the name. Return "
              "ONLY a JSON object mapping each exact name to its cuisine string.\n\n" + "\n".join(names))
    body = json.dumps({"model": model, "max_tokens": 4096,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=90).read())
        txt = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")
        return json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
    except Exception:
        return {}


if __name__ == "__main__":
    for nm in ["Shiso Sushi", "El Toro Taqueria", "Trattoria Napoli", "Froggers Grill & Bar", "Blvck Cat Bistro"]:
        print("%-24s -> %s" % (nm, classify(nm)[0] or "(unsure)"))
