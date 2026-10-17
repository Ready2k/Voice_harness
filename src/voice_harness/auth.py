"""Operator/reviewer roles and non-loopback bind policy (HD-02, SE-01)."""

import hmac
import os
import secrets
from pathlib import Path

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def load_token(root: Path, name: str) -> str:
    """Return the 0600 token file `name` under root, generating it once."""
    path = Path(root) / name
    if not path.exists():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w") as stream:
                stream.write(secrets.token_urlsafe(32))
    return path.read_text().strip()


def role_for(authorization, operator_token, reviewer_token):
    """Constant-time role lookup: 'operator', 'reviewer' or None."""
    if not authorization:
        return None
    if hmac.compare_digest(authorization, "Bearer " + operator_token):
        return "operator"
    if reviewer_token and hmac.compare_digest(authorization, "Bearer " + reviewer_token):
        return "reviewer"
    return None


def reviewer_may(method: str) -> bool:
    """Reviewers are read-only: safe methods only."""
    return method in {"GET", "HEAD"}


def restrict_bundle(bundle: dict) -> dict:
    """Reviewer export: ledger and manifest, with raw audio bytes withheld."""
    restricted = dict(bundle)
    restricted["artifacts"] = [
        {**a, "data_hex": None, "available": False, "withheld_for_role": "reviewer"}
        for a in bundle.get("artifacts", [])
    ]
    manifest = dict(bundle.get("manifest", {}))
    manifest["export_type"] = "reviewer_restricted"
    restricted["manifest"] = manifest
    return restricted


def tls_options(host: str, environ=os.environ) -> dict:
    """uvicorn TLS kwargs; refuse non-loopback binds without TLS.

    A container that binds 0.0.0.0 but is published on host loopback only may set
    HARNESS_PUBLISHED_LOOPBACK_ONLY=1 to assert the exposure is loopback.
    """
    cert, key = environ.get("HARNESS_TLS_CERT"), environ.get("HARNESS_TLS_KEY")
    if cert and key:
        for path in (cert, key):
            if not Path(path).is_file():
                raise RuntimeError("TLS_FILE_MISSING: " + path)
        return {"ssl_certfile": cert, "ssl_keyfile": key}
    if host in LOOPBACK or environ.get("HARNESS_PUBLISHED_LOOPBACK_ONLY") == "1":
        return {}
    raise RuntimeError(
        "TLS_REQUIRED: non-loopback HARNESS_BIND needs HARNESS_TLS_CERT and HARNESS_TLS_KEY"
    )
