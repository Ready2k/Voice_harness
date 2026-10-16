from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ApprovalUnit:
    unit_id: str
    response_id: str
    audio_text: str | bytes
    audio_sha256: str
    generation_epoch: int
    assessment_epoch: int
    sample_range: tuple[int, int]


@dataclass(frozen=True)
class ReleaseToken:
    token_id: str
    unit_id: str
    audio_sha256: str
    client_session_id: str
    generation_epoch: int
    assessment_epoch: int
    expires_at: float


class PlaybackGate:
    def __init__(self, client_session_id: str, *, max_buffer_bytes: int = 1048576):
        self.client_session_id = client_session_id
        self._pending: dict[str, ApprovalUnit] = {}
        self._epoch = 1
        self._issued: dict[str, ReleaseToken] = {}
        self.max_buffer_bytes = max_buffer_bytes
        self._partial: set[str] = set()

    def hold(
        self,
        response_id: str,
        audio_text: str | bytes,
        *,
        sample_range=(0, 1),
        transcript_final=True,
    ) -> ApprovalUnit:
        if not 0 <= sample_range[0] < sample_range[1]:
            raise ValueError("INVALID_SAMPLE_RANGE")
        encode = lambda value: value.encode() if isinstance(value, str) else value
        size = sum(len(encode(u.audio_text)) for u in self._pending.values())
        if size + len(encode(audio_text)) > self.max_buffer_bytes:
            raise ValueError("AUDIO_BUFFER_OVERFLOW")
        digest = hashlib.sha256(encode(audio_text)).hexdigest()
        unit = ApprovalUnit(
            f"unit_{uuid.uuid4().hex[:12]}",
            response_id,
            audio_text,
            digest,
            self._epoch,
            1,
            sample_range,
        )
        self._pending[unit.unit_id] = unit
        if not transcript_final:
            self._partial.add(unit.unit_id)
        return unit

    def approve(
        self, unit: ApprovalUnit, *, policy_version="strict-v1", ttl_seconds=30
    ) -> ReleaseToken:
        current = self._pending.get(unit.unit_id)
        if current != unit:
            raise ValueError("STALE_OR_UNKNOWN_APPROVAL_UNIT")
        if unit.unit_id in self._partial:
            raise ValueError("PARTIAL_TRANSCRIPT")
        token = ReleaseToken(
            f"tok_{uuid.uuid4().hex[:12]}",
            unit.unit_id,
            unit.audio_sha256,
            self.client_session_id,
            unit.generation_epoch,
            unit.assessment_epoch,
            time.time() + ttl_seconds,
        )
        self._issued[token.token_id] = token
        return token

    def release(self, token: ReleaseToken) -> str:
        unit = self._pending.get(token.unit_id)
        if (
            self._issued.get(token.token_id) != token
            or unit is None
            or token.client_session_id != self.client_session_id
            or token.expires_at <= time.time()
        ):
            raise ValueError("INVALID_RELEASE_TOKEN")
        if (
            token.audio_sha256 != unit.audio_sha256
            or token.generation_epoch != self._epoch
            or token.assessment_epoch != unit.assessment_epoch
        ):
            raise ValueError("STALE_RELEASE_TOKEN")
        del self._pending[unit.unit_id]
        self._issued = {
            k: v for k, v in self._issued.items() if v.unit_id != unit.unit_id
        }
        return unit.audio_text

    def revise(self, unit_id: str, revised_text: str) -> ApprovalUnit:
        unit = self._pending[unit_id]
        replacement = replace(
            unit,
            unit_id=f"unit_{uuid.uuid4().hex[:12]}",
            audio_text=revised_text,
            audio_sha256=hashlib.sha256(revised_text.encode()).hexdigest(),
            assessment_epoch=unit.assessment_epoch + 1,
        )
        size = sum(
            len(u.audio_text.encode()) for k, u in self._pending.items() if k != unit_id
        )
        if size + len(revised_text.encode()) > self.max_buffer_bytes:
            raise ValueError("AUDIO_BUFFER_OVERFLOW")
        del self._pending[unit_id]
        self._partial.discard(unit_id)
        self._issued = {k: v for k, v in self._issued.items() if v.unit_id != unit_id}
        self._pending[replacement.unit_id] = replacement
        return replacement

    def interrupt(self) -> None:
        self._epoch += 1
        self._pending.clear()
        self._issued.clear()
        self._partial.clear()
