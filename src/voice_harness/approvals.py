"""Server-owned Ed25519 approvals; clients receive only the public key."""

import base64
import hashlib
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def signing_bytes(token):
    return json.dumps(
        {k: v for k, v in token.items() if k != "signature"},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def key(secret):
    return Ed25519PrivateKey.from_private_bytes(
        hashlib.sha256(secret.encode()).digest()
    )


def public_key(secret):
    return base64.b64encode(
        key(secret).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode()


def sign(token, secret):
    return {
        **token,
        "signature": base64.b64encode(key(secret).sign(signing_bytes(token))).decode(),
    }
