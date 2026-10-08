#!/usr/bin/env bash
# Save / restore the SQLite world state through GitHub Actions artifacts so
# player progress survives runner restarts (each bot cycle runs on a fresh VM).
#
# Usage:  bash deploy/gha_state.sh restore | save
# Needs:  GITHUB_TOKEN + GITHUB_REPOSITORY in the environment (Actions provides both).
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
    if curl -sSL -H "Authorization: Bearer ${GITHUB_TOKEN}" -o /tmp/grimhaven-db.zip "$url"; then
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
  local dir base tmpzip upload_url
  dir=$(dirname "$DB_PATH"); base=$(basename "$DB_PATH"); tmpzip=/tmp/grimhaven-db.zip
  rm -f "$tmpzip"
  (cd "$dir" && zip -q "$tmpzip" "$base") || { echo "⚠️ zip failed"; return 0; }
  upload_url=$(api -X POST \
    "https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/artifacts?name=${ARTIFACT_NAME}&expiration_days=1" \
    | python3 -c 'import sys, json; print(json.load(sys.stdin).get("location", ""))')
  if [ -n "$upload_url" ]; then
    if curl -sS -X PUT -H "Content-Type: application/zip" --data-binary @"$tmpzip" "$upload_url" >/dev/null; then
      echo "💾 world state saved ($(date -u +%H:%M:%SZ))"
    else
      echo "⚠️  state upload failed"
    fi
  else
    echo "⚠️  could not create artifact for state save"
  fi
}

case "${1:-}" in
  restore) restore_state ;;
  save)    save_state ;;
  *) echo "usage: gha_state.sh restore|save"; exit 1 ;;
esac
