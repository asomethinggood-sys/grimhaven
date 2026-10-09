#!/usr/bin/env bash
# Save / restore the SQLite world state through GitHub Actions artifacts so
# player progress survives runner restarts (each bot cycle runs on a fresh VM).
#
# Usage:  bash deploy/gha_state.sh restore | save
# Needs:  GITHUB_TOKEN + GITHUB_REPOSITORY in the environment (restore), and
#         ACTIONS_RUNTIME_TOKEN + ACTIONS_RESULTS_URL (save — Actions provides
#         both to every job automatically).
#
# IMPORTANT (round-4 fix): the artifacts v2 backend is a Twirp RPC service at
# $ACTIONS_RESULTS_URL, NOT the public REST API. The public endpoint
# `POST /repos/…/actions/artifacts` does not exist (404), which is why every
# historical save silently printed "could not create artifact for state save"
# and the game's only database was never persisted between runs. `save` now
# speaks the same protocol as @actions/artifact v2 (the lib behind
# actions/upload-artifact@v4): CreateArtifact → PUT zip to the signed blob
# URL → FinalizeArtifact. Backend ids come from the runtime token's
# `Actions.Results:<run-backend-id>:<job-backend-id>` scope claim.
set -uo pipefail

DB_PATH="${DATABASE_PATH:-data/grimhaven.db}"
ARTIFACT_NAME="grimhaven-db"

api() {
  curl -sS -H "Authorization: Bearer ${GITHUB_TOKEN}" \
       -H "Accept: application/vnd.github+json" "$@"
}

restore_state() {
  mkdir -p "$(dirname "$DB_PATH")"
  local listing url
  listing=$(api "https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/artifacts?name=${ARTIFACT_NAME}&per_page=20")
  url=$(printf '%s' "$listing" | python3 -c '
import sys, json
d = json.load(sys.stdin)
arts = sorted(d.get("artifacts", []), key=lambda a: a.get("created_at", ""), reverse=True)
arts = [a for a in arts if not a.get("expired", False)]
print(arts[0]["archive_download_url"] if arts else "")
')
  if [ -n "$url" ]; then
    if curl -sSL -H "Authorization: Bearer $GITHUB_TOKEN" -o /tmp/grimhaven-db.zip "$url"; then
      if unzip -o /tmp/grimhaven-db.zip -d "$(dirname "$DB_PATH")" >/dev/null 2>&1; then
        echo "♻️  world state restored from last save"
      else
        echo "⚠️  could not unpack state — starting a fresh world"
      fi
    else
      echo "⚠️  state download failed — starting a fresh world"
    fi
  else
    echo "🌱 no previous state — a fresh world is born"
  fi
}

save_state() {
  if [ ! -f "$DB_PATH" ]; then
    echo "💾 nothing to save yet"
    return 0
  fi
  local dir base tmpzip snapshot stagedir
  dir=$(dirname "$DB_PATH"); base=$(basename "$DB_PATH"); tmpzip=/tmp/grimhaven-db.zip
  snapshot=/tmp/grimhaven-snapshot.db
  stagedir=/tmp/grimhaven-zipstage
  rm -f "$tmpzip" "$snapshot"
  rm -rf "$stagedir"; mkdir -p "$stagedir"
  # consistent snapshot via the sqlite backup API when available (the bot keeps
  # writing while we archive); plain copy as a fallback
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$DB_PATH" ".backup '$snapshot'" >/dev/null 2>&1 || cp "$DB_PATH" "$snapshot"
  else
    cp "$DB_PATH" "$snapshot"
  fi
  # the archive must contain the db under its real name so restore unpacks it
  cp "$snapshot" "$stagedir/$base"
  (cd "$stagedir" && zip -q "$tmpzip" "$base") || { echo "⚠️ zip failed"; return 0; }
  python3 - "$tmpzip" "$ARTIFACT_NAME" <<'PY' || { echo "⚠️ state upload failed (non-fatal)"; return 0; }
import base64, hashlib, json, os, sys, urllib.request

zip_path, name = sys.argv[1], sys.argv[2]
token = os.environ.get("ACTIONS_RUNTIME_TOKEN", "")
results_url = os.environ.get("ACTIONS_RESULTS_URL", "")
if not token or not results_url:
    print("⚠️  ACTIONS_RUNTIME_TOKEN / ACTIONS_RESULTS_URL missing — cannot save world state")
    sys.exit(1)


def jwt_claims(tok):
    payload = tok.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


backend = None
for scope in (jwt_claims(token).get("scp") or "").split(" "):
    parts = scope.split(":")
    if len(parts) == 3 and parts[0] == "Actions.Results":
        backend = (parts[1], parts[2])
        break
if not backend:
    print("⚠️  runtime token carries no Actions.Results scope — cannot save world state")
    sys.exit(1)
run_backend_id, job_backend_id = backend

with open(zip_path, "rb") as fh:
    data = fh.read()
origin = results_url.rstrip("/")


def twirp(method, body):
    req = urllib.request.Request(
        f"{origin}/twirp/github.actions.results.api.v1.ArtifactService/{method}",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json",
                 "Accept": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


created = twirp("CreateArtifact", {
    "workflow_run_backend_id": run_backend_id,
    "workflow_job_run_backend_id": job_backend_id,
    "name": name,
    "version": 4,
})
if not created.get("ok"):
    print("⚠️  CreateArtifact rejected:", created)
    sys.exit(1)
upload_url = created.get("signed_upload_url") or ""
if not upload_url:
    print("⚠️  CreateArtifact returned no signed upload url")
    sys.exit(1)

req = urllib.request.Request(
    upload_url, data=data, method="PUT",
    headers={"Content-Type": "zip", "x-ms-blob-type": "BlockBlob"})
with urllib.request.urlopen(req, timeout=300) as resp:
    if resp.status not in (200, 201):
        print("⚠️  blob upload returned status", resp.status)
        sys.exit(1)

final = twirp("FinalizeArtifact", {
    "workflow_run_backend_id": run_backend_id,
    "workflow_job_run_backend_id": job_backend_id,
    "name": name,
    "size": str(len(data)),
    "hash": {"value": f"sha256:{hashlib.sha256(data).hexdigest()}"},
})
if not final.get("ok"):
    print("⚠️  FinalizeArtifact rejected:", final)
    sys.exit(1)
print(f"💾 world state saved (artifact #{final.get('artifact_id')}, {len(data)} bytes)")
PY
}

case "${1:-}" in
  restore) restore_state ;;
  save)    save_state ;;
  *) echo "usage: gha_state.sh restore|save"; exit 1 ;;
esac
