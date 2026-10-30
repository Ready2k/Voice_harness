"""AC-08 signature gate: Ed25519 over the RFC 8785 bytes of the evidence manifest.

Trust model: the bundle carries a detached envelope ``{algorithm, key_id,
payload_sha256, value}``; the manifest names the same key id and algorithm, so
the key choice is itself signed.  Verification is offline and only trusts public
keys the verifier supplies (a PEM key or a key-ring JSON from ``public_keys()``).
Private keys live in the data directory with mode 0600.  Rotation retires the
active key (private half deleted, public half kept in an append-only,
hash-chained history so old bundles still verify); revocation is permanent and
makes every signature by that key fail verification.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .canonical import canonical_json

ALGORITHM = "Ed25519"
STATUS_SIGNED = "signed_ed25519"
STATUS_UNSIGNED = "unsigned_local"
ENVELOPE_LOCATION = "bundle.signature"


class SigningError(Exception):
    """Carries a stable reason code (first argument)."""


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _raw_public(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def key_id_for(public_raw: bytes) -> str:
    return "ed25519-" + hashlib.sha256(public_raw).hexdigest()[:32]


class KeyStore:
    """Local signing keys: ``<dir>/signing/{keys/<id>.pem, key-history.jsonl}``."""

    def __init__(self, data_dir, clock=time.time):
        self.dir = Path(data_dir) / "signing"
        self.keys = self.dir / "keys"
        self.history_path = self.dir / "key-history.jsonl"
        self.clock = clock
        self._lock = threading.RLock()
        self.keys.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._lock:
            if not self._records():
                self._generate("initial key generated")

    # -- history (append-only, hash chained) --------------------------------
    def _records(self) -> list[dict]:
        if not self.history_path.exists():
            return []
        records, previous = [], None
        for line in self.history_path.read_text().splitlines():
            record = json.loads(line)
            body = {k: v for k, v in record.items() if k != "record_hash"}
            expected = hashlib.sha256(canonical_json(body)).hexdigest()
            if record["prev_hash"] != previous or record["record_hash"] != expected:
                raise SigningError("KEY_HISTORY_CORRUPT")
            previous = record["record_hash"]
            records.append(record)
        return records

    def _append(self, **fields) -> dict:
        records = self._records()
        body = {
            "seq": len(records) + 1,
            "at": self.clock(),
            "prev_hash": records[-1]["record_hash"] if records else None,
            **fields,
        }
        record = {**body, "record_hash": hashlib.sha256(canonical_json(body)).hexdigest()}
        fd = os.open(self.history_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return record

    def history(self) -> list[dict]:
        with self._lock:
            return self._records()

    def _state(self) -> dict[str, dict]:
        keys: dict[str, dict] = {}
        for record in self._records():
            kid = record["key_id"]
            if record["event"] == "generated":
                keys[kid] = {
                    "key_id": kid,
                    "algorithm": record["algorithm"],
                    "public_key": record["public_key"],
                    "status": "active",
                    "created_at": record["at"],
                    "retired_at": None,
                    "revoked_at": None,
                    "revocation_reason": None,
                }
            elif record["event"] == "retired":
                keys[kid]["status"] = "retired"
                keys[kid]["retired_at"] = record["at"]
            elif record["event"] == "revoked":
                keys[kid]["status"] = "revoked"
                keys[kid]["revoked_at"] = record["at"]
                keys[kid]["revocation_reason"] = record["reason"]
        return keys

    # -- key lifecycle -------------------------------------------------------
    def _generate(self, reason: str) -> str:
        private = Ed25519PrivateKey.generate()
        raw = _raw_public(private.public_key())
        kid = key_id_for(raw)
        pem = private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        fd = os.open(self.keys / (kid + ".pem"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(pem)
            stream.flush()
            os.fsync(stream.fileno())
        self._append(
            event="generated",
            key_id=kid,
            algorithm=ALGORITHM,
            public_key=_b64(raw),
            reason=reason,
        )
        return kid

    def active_key_id(self) -> str:
        with self._lock:
            active = [k for k in self._state().values() if k["status"] == "active"]
            if len(active) != 1:
                raise SigningError("NO_ACTIVE_SIGNING_KEY")
            return active[0]["key_id"]

    def _retire(self, kid: str, reason: str):
        self._append(event="retired", key_id=kid, reason=reason)
        # The private half of a retired key is destroyed; the public half remains.
        (self.keys / (kid + ".pem")).unlink(missing_ok=True)

    def import_public_key(self, key_id: str, public_key: str, reason: str) -> bool:
        """Trust another store's key for verification only: recorded as generated then retired, with no private half.

        Returns False when the key is already known."""
        with self._lock:
            if key_id in self._state():
                return False
            raw = base64.b64decode(public_key)
            if key_id_for(raw) != key_id:
                raise SigningError("KEYRING_KEY_ID_MISMATCH")
            self._append(event="generated", key_id=key_id, algorithm=ALGORITHM, public_key=public_key, reason=reason)
            self._append(event="retired", key_id=key_id, reason="imported verification-only")
            return True

    def rotate(self, reason: str = "scheduled rotation") -> str:
        with self._lock:
            old = self.active_key_id()
            new = self._generate("rotation: " + reason)
            self._retire(old, "rotated to " + new)
            return new

    def revoke(self, key_id: str, reason: str) -> dict:
        """Permanent. Revoking the active key first rotates to a fresh one."""
        with self._lock:
            state = self._state()
            if key_id not in state:
                raise SigningError("UNKNOWN_KEY")
            if state[key_id]["status"] == "revoked":
                return state[key_id]
            if state[key_id]["status"] == "active":
                self._generate("replacement for revoked key " + key_id)
                self._retire(key_id, "revoked")
            self._append(event="revoked", key_id=key_id, reason=reason)
            return self._state()[key_id]

    def public_keys(self) -> dict:
        """Key ring for distribution to verifiers (public material only)."""
        with self._lock:
            keys = list(self._state().values())
            return {
                "algorithm": ALGORITHM,
                "active_key_id": next(
                    (k["key_id"] for k in keys if k["status"] == "active"), None
                ),
                "keys": keys,
                "revoked": [k["key_id"] for k in keys if k["status"] == "revoked"],
                "history_head": (self._records() or [{}])[-1].get("record_hash"),
            }

    def signer(self) -> Signer:
        with self._lock:
            kid = self.active_key_id()
            pem = (self.keys / (kid + ".pem")).read_bytes()
            return Signer(kid, serialization.load_pem_private_key(pem, None))


class Signer:
    def __init__(self, key_id, private_key):
        self.key_id, self._key = key_id, private_key

    def seal(self, manifest: dict) -> dict:
        """Stamp the manifest's signature section and return the envelope.

        The key id and algorithm are written into the manifest first so they are
        covered by the signature."""
        manifest["signature"] = {
            "algorithm": ALGORITHM,
            "key_id": self.key_id,
            "envelope": ENVELOPE_LOCATION,
        }
        manifest["signature_status"] = STATUS_SIGNED
        payload = canonical_json(manifest)
        return {
            "algorithm": ALGORITHM,
            "key_id": self.key_id,
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "value": _b64(self._key.sign(payload)),
        }


def mark_unsigned(manifest: dict) -> None:
    manifest["signature"] = None
    manifest["signature_status"] = STATUS_UNSIGNED


# -- offline verification ----------------------------------------------------
@dataclass
class Keyring:
    keys: dict = field(default_factory=dict)
    revoked: set = field(default_factory=set)


def load_keyring(source) -> Keyring:
    """Trusted keys from a path, text, bytes or dict.

    Accepts a PEM public key, a base64 raw Ed25519 public key, or the JSON from
    ``KeyStore.public_keys()`` (which also carries the revocation list)."""
    if isinstance(source, Keyring):
        return source
    if isinstance(source, Path):
        source = source.read_bytes()
    if isinstance(source, bytes):
        source = source.decode("utf-8")
    if isinstance(source, str):
        source = source.strip()
        if source.startswith("{"):
            source = json.loads(source)
    ring = Keyring()
    try:
        if isinstance(source, dict):
            for entry in source.get("keys", []):
                if entry.get("algorithm") != ALGORITHM:
                    continue
                raw = base64.b64decode(entry["public_key"])
                if key_id_for(raw) != entry["key_id"]:
                    raise SigningError("KEYRING_KEY_ID_MISMATCH")
                ring.keys[entry["key_id"]] = Ed25519PublicKey.from_public_bytes(raw)
                if entry.get("status") == "revoked":
                    ring.revoked.add(entry["key_id"])
            ring.revoked.update(source.get("revoked", []))
            return ring
        if source.startswith("-----BEGIN"):
            key = serialization.load_pem_public_key(source.encode())
            if not isinstance(key, Ed25519PublicKey):
                raise SigningError("UNSUPPORTED_KEY_TYPE")
        else:
            key = Ed25519PublicKey.from_public_bytes(base64.b64decode(source))
    except (binascii.Error, KeyError, TypeError, ValueError) as error:
        raise SigningError("KEYRING_INVALID") from error
    ring.keys[key_id_for(_raw_public(key))] = key
    return ring


def verify_envelope(manifest, envelope, ring: Keyring) -> str:
    """Return ``"ok"`` or a stable reason code."""
    try:
        if not isinstance(envelope, dict):
            return "SIGNATURE_MISSING"
        if envelope.get("algorithm") != ALGORITHM:
            return "SIGNATURE_ALGORITHM_UNSUPPORTED"
        kid = envelope.get("key_id")
        declared = manifest.get("signature")
        if (
            not isinstance(declared, dict)
            or declared.get("key_id") != kid
            or declared.get("algorithm") != ALGORITHM
            or manifest.get("signature_status") != STATUS_SIGNED
        ):
            return "SIGNATURE_MANIFEST_MISMATCH"
        if kid in ring.revoked:
            return "KEY_REVOKED"
        key = ring.keys.get(kid)
        if key is None:
            return "KEY_UNKNOWN"
        payload = canonical_json(manifest)
        if envelope.get("payload_sha256") != hashlib.sha256(payload).hexdigest():
            return "SIGNATURE_INVALID"
        key.verify(base64.b64decode(envelope["value"], validate=True), payload)
        return "ok"
    except (InvalidSignature, binascii.Error, KeyError, TypeError, ValueError):
        return "SIGNATURE_INVALID"


def check_bundle_signature(bundle, keyring=None, *, allow_unsigned=False, revoked=()) -> str:
    """Signature policy for a whole bundle; strict unless ``allow_unsigned``.

    ``allow_unsigned`` only excuses a bundle that honestly declares
    ``unsigned_local`` and carries no signature.  A bundle that carries a
    signature is always verified, and one that claims to be signed but lacks
    the envelope always fails."""
    manifest = bundle.get("manifest", {})
    envelope = bundle.get("signature")
    if envelope is None:
        if manifest.get("signature_status") == STATUS_SIGNED or manifest.get("signature"):
            return "SIGNATURE_MISSING"
        return "ok" if allow_unsigned else "UNSIGNED_BUNDLE"
    if keyring is None:
        return "NO_TRUSTED_KEY"
    ring = load_keyring(keyring)
    ring = Keyring(dict(ring.keys), set(ring.revoked) | set(revoked))
    result = verify_envelope(manifest, envelope, ring)
    if result != "ok":
        return result
    if "source_manifest" in bundle:
        # Derived view: the source manifest it links to must be signed and trusted too.
        result = verify_envelope(
            bundle["source_manifest"], bundle.get("source_signature"), ring
        )
        if result != "ok":
            return "SOURCE_" + result
    return "ok"
