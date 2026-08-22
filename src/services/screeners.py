"""Screener implementations. Spec section 6.5, with the path correction in 19.1.

Publisher free text is screened by calling Model Armor's `SanitizeUserPrompt`
directly, not through Agent Gateway: gateway egress screening covers agent
traffic to LLMs, MCP servers and other agents, and a WZDx feed fetch is a plain
HTTPS GET of GeoJSON. The gateway still governs Interchange's own outbound Gemini
calls. M1 confirms or overturns this (section 19.7).

Every implementation here fails closed. That is not a style preference: a
screener returning PASS when it could not reach the service breaks the invariant
the entire security claim rests on, and it breaks it silently.
"""

from __future__ import annotations

from .ports import ScreenVerdict


class FailClosedScreener:
    """The default when no screening service is configured.

    Blocks everything. That sounds useless and is exactly right: section 6.5 says
    an unavailable Model Armor is treated identically to a block, so a system with
    no screener configured must redact all free text rather than pass it through.
    Making this the default means forgetting to configure a screener produces
    visibly redacted output rather than an invisible security hole.
    """

    policy_version = "fail-closed"
    model_version = "none"

    def screen(self, text: str) -> tuple[ScreenVerdict, str | None]:
        del text
        return "BLOCK", "no-screener-configured"


class AllowAllScreener:
    """Testing only. Never construct this outside a test.

    Exists so tests of the reconciler and republisher are not drowned in
    redaction placeholders when what they are testing is geometry or conflict
    resolution. It deliberately carries an alarming policy_version so that a
    ScreeningResult produced by it can never be mistaken for a real verdict, and
    so a cached verdict from a test can never satisfy a production cache lookup.
    """

    policy_version = "INSECURE-TEST-ONLY"
    model_version = "none"

    def screen(self, text: str) -> tuple[ScreenVerdict, str | None]:
        del text
        return "PASS", None


class KeywordScreener:
    """Offline stand-in with real behaviour, for building against before M1.

    Not a security control and not a Model Armor substitute. It exists so the
    screening PATH can be exercised end to end without cloud access: cache keying,
    redaction placeholders, the audit record, and the invariant that a block never
    moves a trust score. Swap for ModelArmorScreener once M1 lands.

    The patterns are drawn from the injection classes Model Armor documents, so a
    synthetic injected description written for the demo trips this too.
    """

    policy_version = "keyword-v1"
    model_version = "none"

    PATTERNS: tuple[tuple[str, str], ...] = (
        ("ignore previous", "prompt-injection"),
        ("ignore all previous", "prompt-injection"),
        ("disregard the above", "prompt-injection"),
        ("system prompt", "prompt-injection"),
        ("you are now", "jailbreak"),
        ("act as", "jailbreak"),
        ("new instructions", "prompt-injection"),
        ("</system>", "prompt-injection"),
        ("<|im_start|>", "prompt-injection"),
    )

    def screen(self, text: str) -> tuple[ScreenVerdict, str | None]:
        lowered = text.lower()
        for needle, category in self.PATTERNS:
            if needle in lowered:
                return "BLOCK", category
        return "PASS", None


# Emitted in place of blocked or unscreenable text. Section 6.8: road_names and
# direction are REQUIRED by the WZDx 4.2 schema, so a blocked value cannot be
# dropped and must be replaced by something schema-valid and visibly not real.
REDACTION_PLACEHOLDER = "[redacted: failed screening]"


def redact(_text: str) -> str:
    return REDACTION_PLACEHOLDER
