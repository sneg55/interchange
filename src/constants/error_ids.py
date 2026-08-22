"""Central error ID registry. See guides/error-id-registry.md.

Rules:
  1. Never reuse a retired ID - mark it `# retired` and leave it in place.
  2. One ID per distinct cause, not per raise site.
  3. Numbers are stable; append, never renumber.
  4. Domain prefix (3-5 letters) is required.

Raise via AppError(ErrorIds.X, "...", {...}). Log lines include the ID so
grep, telemetry, and agents can all find every occurrence with one search.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any


class ErrorIds(StrEnum):
    # ── Config (CFG) ─────────────────────────────────────────────────────────
    CFG_MISSING = "E_CFG_001"
    CFG_INVALID_JSON = "E_CFG_002"
    CFG_SCHEMA_FAIL = "E_CFG_003"
    CFG_ENV_MISSING = "E_CFG_004"

    # ── Filesystem (FS) ──────────────────────────────────────────────────────
    FS_NOT_FOUND = "E_FS_001"
    FS_PERMISSION = "E_FS_002"
    FS_DISK_FULL = "E_FS_003"
    FS_READ_FAIL = "E_FS_004"
    FS_WRITE_FAIL = "E_FS_005"

    # ── Network (NET) ────────────────────────────────────────────────────────
    NET_TIMEOUT = "E_NET_001"
    NET_DNS = "E_NET_002"
    NET_TLS = "E_NET_003"
    NET_RATE_LIMITED = "E_NET_004"
    NET_UNAVAILABLE = "E_NET_005"
    NET_BAD_SHAPE = "E_NET_006"

    # ── Tool execution (TOOL) ────────────────────────────────────────────────
    TOOL_ABORTED = "E_TOOL_001"
    TOOL_BAD_INPUT = "E_TOOL_002"
    TOOL_TIMEOUT = "E_TOOL_003"
    TOOL_PERMISSION_DENIED = "E_TOOL_004"
    TOOL_SECURITY_BLOCKED = "E_TOOL_005"

    # ── LLM / API (LLM) ──────────────────────────────────────────────────────
    LLM_RATE_LIMITED = "E_LLM_001"
    LLM_CONTEXT_OVERFLOW = "E_LLM_002"
    LLM_BAD_RESPONSE = "E_LLM_003"

    # ── Registry (REG) ───────────────────────────────────────────────────────
    REG_FETCH_FAIL = "E_REG_001"
    REG_BAD_SHAPE = "E_REG_002"
    REG_SHORT_READ = "E_REG_003"  # pull returned < half the known entries; not a delisting
    REG_DUPLICATE_KEY = "E_REG_004"  # (org, feedname) collided; the key is meant to be unique

    # ── Feed polling (FEED) ──────────────────────────────────────────────────
    FEED_UNREACHABLE = "E_FEED_001"
    FEED_TIMEOUT = "E_FEED_002"
    FEED_BAD_JSON = "E_FEED_003"
    FEED_NO_HEADER = "E_FEED_004"
    FEED_UNPARSEABLE_TIMESTAMP = "E_FEED_005"  # drives R6; not a transport failure
    FEED_NO_CARRY_FORWARD = "E_FEED_006"  # 304 with no prior body to carry forward from

    # ── Schema conformance (SCHEMA) ──────────────────────────────────────────
    SCHEMA_FETCH_FAIL = "E_SCHEMA_001"
    SCHEMA_UNKNOWN_VERSION = "E_SCHEMA_002"  # suppresses R3; never penalises the publisher
    SCHEMA_VALIDATION_FAIL = "E_SCHEMA_003"

    # ── Trust scoring (TRUST) ────────────────────────────────────────────────
    TRUST_RULE_UNEVALUABLE = "E_TRUST_001"  # yields NOT_APPLICABLE, never ADMIT
    TRUST_UNKNOWN_RULESET = "E_TRUST_002"
    TRUST_ILLEGAL_TRANSITION = "E_TRUST_003"

    # ── Screening (SCREEN) ───────────────────────────────────────────────────
    # Every one of these results in redaction. None may result in pass-through.
    SCREEN_UNAVAILABLE = "E_SCREEN_001"
    SCREEN_BLOCKED = "E_SCREEN_002"
    SCREEN_TIMEOUT = "E_SCREEN_003"
    SCREEN_TEXT_TOO_LARGE = "E_SCREEN_004"

    # ── Reconciliation (RECON) ───────────────────────────────────────────────
    RECON_NULL_GEOMETRY = "E_RECON_001"  # counted, never silently dropped
    RECON_AMBIGUOUS_GROUPING = "E_RECON_002"
    RECON_SAME_PUBLISHER_COLLISION = "E_RECON_003"
    RECON_ADJUDICATION_FAILED = "E_RECON_004"  # yields UNSURE, never a guess

    # ── Republishing (PUB) ───────────────────────────────────────────────────
    PUB_SELF_VALIDATION_FAILED = "E_PUB_001"  # do not publish; surface it
    PUB_MISSING_REQUIRED_FIELD = "E_PUB_002"  # exclude and count; never invent a value
    PUB_ZONE_EXCLUDED = "E_PUB_003"
    # The feed passed its own gate and could not be written. Recorded on the
    # artifact rather than failing the cycle: the observations and trust
    # decisions are already correct, and discarding them over a storage error
    # would lose reliability history to fix nothing.
    PUB_SINK_FAILED = "E_PUB_004"

    # ── Evidence and approval (EVID) ─────────────────────────────────────────
    EVID_PACKET_NOT_FOUND = "E_EVID_001"
    EVID_UNAUTHORIZED_APPROVAL = "E_EVID_002"  # approved_by must come from the verified token
    EVID_ALREADY_RESOLVED = "E_EVID_003"

    # ── Store (STORE) ────────────────────────────────────────────────────────
    STORE_UNAVAILABLE = "E_STORE_001"
    STORE_BAD_DOC_ID = "E_STORE_002"  # refuse rather than mangle; two keys must not collide
    STORE_BAD_RECORD = "E_STORE_003"  # a stored document the fleet cannot rebuild; never skipped

    # Add new domains/IDs below. Keep the comment block above each domain.


class AppError(Exception):
    """Base error: every raise carries a stable ID plus structured context."""

    def __init__(
        self,
        error_id: ErrorIds,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.id = error_id
        self.context = context or {}

    def to_log_line(self) -> str:
        ctx = " ".join(f"{k}={json.dumps(v, default=str)}" for k, v in self.context.items())
        return f"[{self.id}] {self}{' ' + ctx if ctx else ''}"
