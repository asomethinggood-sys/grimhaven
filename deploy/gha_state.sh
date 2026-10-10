#!/usr/bin/env bash
# Save / restore the SQLite world state through GitHub Actions artifacts so
# player progress survives runner restarts (each bot cycle runs on a fresh VM).
#
# IMPORTANT: the artifacts v2 backend is a Twirp RPC service at
# $ACTIONS_RESULTS_URL, NOT the public REST API. `POST /repos/…/actions/artifacts`
# does not exist (404). We speak the protocol of @actions/artifact v2
# (CreateArtifact → PUT zip → FinalizeArtifact) from deploy/gha_artifact.py.
#
# Every historical failure here was hidden behind `|| true`, so the run stayed
# green while the game's only database was never stored — each runner restart
# silently wiped every player's progress. `save` now returns non-zero on
# failure, and `verify` turns "nothing was stored" into a visible red run.
#
# Usage:  bash deploy/gha_state.sh restore | save | verify
# Needs:  GITHUB_TOKEN + GITHUB_REPOSITORY (restore/verify), and
#         ACTIONS_RUNTIME_TOKEN + ACTIONS_RESULTS_URL (save).

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
  (cd "$stagedir" && zip -q "$tmpzip" "$base") || {
    echo "❌ could not build the state archive (zip failed)"; return 1; }
  if ! python3 "$(dirname "$0")/gha_artifact.py" save "$tmpzip" "$ARTIFACT_NAME"; then
    echo "❌ world state could not be saved — player progress will be LOST at the"
    echo "   next runner restart. See the step log above for the exact reason."
    return 1
  fi
}

case "${1:-}" in
  restore) restore_state ;;
  save)    save_state ;;
  verify)  python3 "$(dirname "$0")/gha_artifact.py" verify "$ARTIFACT_NAME" ;;
  *) echo "usage: gha_state.sh restore|save|verify"; exit 1 ;;
esac
