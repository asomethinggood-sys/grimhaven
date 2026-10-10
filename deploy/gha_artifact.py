#!/usr/bin/env python3
"""GitHub Actions artifact client for the Grimhaven world state.

The runner is ephemeral (each bot cycle boots a fresh VM), so the SQLite
database is shipped out through Actions artifacts. The artifacts v2 backend is
a Twirp RPC service at ``$ACTIONS_RESULTS_URL`` — NOT the public REST API
(``POST /repos/…/actions/artifacts`` does not exist and 404s). We speak the same
protocol as ``@actions/artifact`` v2:

    CreateArtifact → PUT the zip to the signed blob URL → FinalizeArtifact

Backend ids come from the ``Actions.Results:<run>:<job>`` scope claim on the
runtime token.

This lived inside ``gha_state.sh`` as a heredoc, where every failure was
swallowed by a ``|| true`` and the run still went green — so the database
silently stopped being saved and nobody noticed. It is a module now so the
protocol is unit-testable and every error path prints *why* it failed.

Usage:
    python3 deploy/gha_artifact.py save <zip> <name>
    python3 deploy/gha_artifact.py verify <name>   # exit 1 if nothing stored
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

ARTIFACT_VERSION = 4


class ArtifactError(RuntimeError):
    """Any failure to store or confirm the world state."""


def _require_env() -> tuple[str, str]:
    token = os.environ.get("ACTIONS_RUNTIME_TOKEN", "")
    results_url = os.environ.get("ACTIONS_RESULTS_URL", "")
    missing = [
        name for name, val in (("ACTIONS_RUNTIME_TOKEN", token),
                               ("ACTIONS_RESULTS_URL", results_url)) if not val
    ]
    if missing:
        raise ArtifactError(
            f"missing environment variable(s): {', '.join(missing)} — "
            "the artifact service is unreachable from this step"
        )
    return token, results_url


def _jwt_claims(tok: str) -> dict:
    parts = tok.split(".")
    if len(parts) < 2:
        raise ArtifactError("runtime token is not a JWT")
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, TypeError) as exc:
        raise ArtifactError(f"runtime token payload is not base64 JSON: {exc}") from exc


def _backend_ids(token: str) -> tuple[str, str]:
    """Pull (run_backend_id, job_backend_id) out of the Actions.Results scope."""
    claims = _jwt_claims(token)
    scopes = claims.get("scp") or claims.get("scope") or ""
    for scope in str(scopes).split(" "):
        parts = scope.strip().split(":")
        if len(parts) == 3 and parts[0] == "Actions.Results":
            return parts[1], parts[2]
    raise ArtifactError(
        "runtime token carries no Actions.Results scope — this step cannot "
        f"write artifacts (claims: {sorted(claims)})"
    )


class ArtifactClient:
    def __init__(self, token: str, results_url: str, timeout: int = 60):
        self.token = token
        self.origin = results_url.rstrip("/")
        self.timeout = timeout

    def _twirp(self, method: str, body: dict) -> dict:
        url = f"{self.origin}/twirp/github.actions.results.api.v1.ArtifactService/{method}"
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            raise ArtifactError(f"{method} → HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ArtifactError(f"{method} → {exc.reason}") from exc

    def create(self, run_id: str, job_id: str, name: str) -> str:
        resp = self._twirp("CreateArtifact", {
            "workflow_run_backend_id": run_id,
            "workflow_job_run_backend_id": job_id,
            "name": name,
            "version": ARTIFACT_VERSION,
        })
        if not resp.get("ok"):
            raise ArtifactError(f"CreateArtifact rejected: {resp}")
        url = resp.get("signed_upload_url") or ""
        if not url:
            raise ArtifactError("CreateArtifact returned no signed upload url")
        return url

    def upload_blob(self, url: str, payload: bytes) -> None:
        req = urllib.request.Request(
            url, data=payload, method="PUT",
            headers={"Content-Type": "zip", "x-ms-blob-type": "BlockBlob"},
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                if resp.status not in (200, 201):
                    raise ArtifactError(f"blob upload returned status {resp.status}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            raise ArtifactError(f"blob upload → HTTP {exc.code}: {detail}") from exc

    def finalize(self, run_id: str, job_id: str, name: str, payload: bytes) -> int:
        resp = self._twirp("FinalizeArtifact", {
            "workflow_run_backend_id": run_id,
            "workflow_job_run_backend_id": job_id,
            "name": name,
            "size": str(len(payload)),
            "hash": {"value": f"sha256:{hashlib.sha256(payload).hexdigest()}"},
        })
        if not resp.get("ok"):
            raise ArtifactError(f"FinalizeArtifact rejected: {resp}")
        return int(resp.get("artifact_id", 0))


def store(zip_path: str, name: str, attempts: int = 3) -> int:
    """Create → upload → finalize, with retries. Returns the artifact id."""
    token, results_url = _require_env()
    run_id, job_id = _backend_ids(token)
    client = ArtifactClient(token, results_url)
    with open(zip_path, "rb") as fh:
        payload = fh.read()
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            upload_url = client.create(run_id, job_id, name)
            client.upload_blob(upload_url, payload)
            artifact_id = client.finalize(run_id, job_id, name, payload)
            print(f"💾 world state saved (artifact #{artifact_id}, "
                  f"{len(payload)} bytes)")
            return artifact_id
        except ArtifactError as exc:
            last = exc
            print(f"⚠️  save attempt {attempt}/{attempts} failed: {exc}")
            if attempt < attempts:
                time.sleep(3 * attempt)
    raise ArtifactError(f"world state could not be saved: {last}")


def verify(name: str, token: str | None = None) -> None:
    """Fail loudly unless at least one artifact with ``name`` exists.

    Guards the silent-data-loss failure mode: a green run that quietly stored
    nothing used to look exactly like a healthy one.
    """
    gh_token = token or os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not gh_token or not repo:
        raise ArtifactError("GITHUB_TOKEN / GITHUB_REPOSITORY missing — cannot verify")
    url = (f"https://api.github.com/repos/{repo}/actions/artifacts"
           f"?name={name}&per_page=20")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise ArtifactError(f"artifact listing → HTTP {exc.code}: {detail}") from exc
    arts = [a for a in body.get("artifacts", []) if not a.get("expired", False)]
    if not arts:
        raise ArtifactError(
            f"no stored artifact named {name!r} — the world state is NOT being "
            "persisted and every player loses progress at the next runner restart"
        )
    latest = max(arts, key=lambda a: a.get("created_at", ""))
    print(f"✅ world state persisted: {latest.get('name')} "
          f"(id {latest.get('id')}, {latest.get('size_in_bytes')} bytes, "
          f"created {latest.get('created_at')})")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    command = argv[1]
    try:
        if command == "save" and len(argv) == 4:
            store(argv[2], argv[3])
        elif command == "verify" and len(argv) == 3:
            verify(argv[2])
        else:
            print(__doc__)
            return 2
    except ArtifactError as exc:
        print(f"❌ {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))