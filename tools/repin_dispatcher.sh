#!/usr/bin/env bash
# repin_dispatcher.sh — point the hourly Fly dispatcher machine at the CURRENT app image.
#
# WHY THIS EXISTS
#   The dispatcher (unifyd/dispatch_ephemeral.py) runs as a Fly SCHEDULED machine tagged
#   metadata role=dispatcher. It deliberately has no process group — which also means
#   `flyctl deploy` never updates it. It therefore keeps running whatever image it was pinned to
#   at creation.
#
#   That is not cosmetic. Due-ness is computed from the dispatcher's OWN copy of
#   source_registry.py, so until it is re-pinned a newly added source is invisible to the
#   scheduler and simply never runs — the pull machines it spawns are fine (they always get the
#   app's current image), but nothing tells it to spawn them.
#
#   Run this after any deploy that adds or changes a source.
#
# USAGE
#   tools/repin_dispatcher.sh [app]          # default app: hoodie-suite
#
# It is idempotent: if the dispatcher already runs the current image it reports that and exits 0
# without touching the machine.

set -euo pipefail

APP="${1:-hoodie-suite}"
FLYCTL="${FLYCTL:-$HOME/.fly/bin/flyctl}"
DISPATCH_CMD="python3 -u /app/unifyd/dispatch_ephemeral.py"

command -v "$FLYCTL" >/dev/null 2>&1 || { echo "flyctl not found at $FLYCTL" >&2; exit 1; }

read -r DISPATCHER APP_IMAGE DISPATCHER_TAG APP_TAG <<EOF
$("$FLYCTL" machines list -a "$APP" --json | python3 -c '
import json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(".")), "unifyd"))

def image_tag(ref):
    """Same normalization as dispatch_ephemeral.image_tag — the SAME release comes back as
    `:deployment-XYZ` on an app machine but `:deployment-XYZ@sha256:...` on a pinned one, so a raw
    string compare would re-pin forever (or, worse, look stale when it is not)."""
    if not ref:
        return ""
    name = ref.rsplit("/", 1)[-1]
    tag = name.split("@", 1)[0]
    if ":" in tag:
        return tag.split(":", 1)[1]
    return name.split("@", 1)[1] if "@" in name else name

ms = json.load(sys.stdin)
dispatcher = app_image = dispatcher_image = ""
for m in ms:
    cfg = m.get("config") or {}
    md = cfg.get("metadata") or {}
    if md.get("role") == "dispatcher":
        dispatcher, dispatcher_image = m["id"], cfg.get("image", "")
    elif md.get("fly_process_group") == "app" and m.get("state") == "started":
        app_image = cfg.get("image", "")
print(dispatcher or "-", app_image or "-", image_tag(dispatcher_image) or "-", image_tag(app_image) or "-")
')
EOF

[ "$DISPATCHER" = "-" ] && { echo "no machine tagged role=dispatcher on $APP" >&2; exit 1; }
[ "$APP_IMAGE" = "-" ] && { echo "no started machine in the 'app' process group on $APP" >&2; exit 1; }

if [ "$DISPATCHER_TAG" = "$APP_TAG" ]; then
  echo "dispatcher $DISPATCHER already on the current release ($APP_TAG) — nothing to do"
  exit 0
fi

echo "re-pinning dispatcher $DISPATCHER"
# Use the NORMALIZED tags read above. `$DISPATCHER_IMAGE` was never assigned (the read pulls
# DISPATCHER_TAG), so this line printed an empty "from:" — losing the one fact the re-pin exists to
# record: which image the scheduler had been stuck on.
echo "  from: ${DISPATCHER_TAG}"
echo "  to:   ${APP_TAG}"

# --schedule/--restart are re-asserted because `machine update` rewrites the whole config.
"$FLYCTL" machine update "$DISPATCHER" -a "$APP" \
  --image "$APP_IMAGE" \
  --command "$DISPATCH_CMD" \
  --schedule hourly \
  --restart no \
  --vm-memory 2048 \
  --yes

echo "done — next hourly tick runs the deployed registry"
