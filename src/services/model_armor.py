"""Model Armor as a Screener. Spec 6.5, with the path correction in 19.1.

Publisher free text is screened by calling `SanitizeUserPrompt` directly rather
than through Agent Gateway. Gateway egress screening covers agent traffic to
LLMs, MCP servers and other agents; a WZDx feed fetch is a plain HTTPS GET of a
GeoJSON document and is none of those. The gateway still governs Interchange's
own outbound Gemini calls.

**Fails closed, and that is the whole point.** Every failure mode here returns
BLOCK: an unreachable service, a timeout, a malformed response, an unrecognised
verdict, text too large to send. A screener returning PASS when it could not
reach the service breaks the invariant the security claim rests on, and it
breaks it silently, which is worse than breaking it loudly.

The caller (`ScreeningGate`) distinguishes a real BLOCK from an outage by the
category string and declines to cache the latter, because an outage is not a
verdict.
"""

from __future__ import annotations

from typing import Any

from src.constants.error_ids import ErrorIds

from .ports import ScreenVerdict

# Model Armor's own request limit. Text beyond it is BLOCKED rather than
# truncated and sent: screening a prefix and passing the whole string is a
# false negative dressed up as a check.
MAX_TEXT_BYTES = 100_000

DEFAULT_TIMEOUT_SECONDS = 10.0


class ModelArmorScreener:
    """Calls Model Armor's `SanitizeUserPrompt` for one template.

    `policy_version` is the template path plus an operator-set revision, so a
    template change invalidates every cached verdict. Both parts are needed: the
    path catches a switch to a different template, and the revision catches an
    in-place edit to the same one, which is the more likely change and the one
    the path cannot see. Text screened under the old filters has NOT been
    screened under the new ones.
    """

    def __init__(
        self,
        project: str,
        location: str,
        template_id: str,
        client: Any = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        revision: str = "r1",
    ) -> None:
        self._template = f"projects/{project}/locations/{location}/templates/{template_id}"
        self._location = location
        self._client = client
        self._timeout = timeout
        self._model_version = "model-armor"
        # An operator-set label for the template's CONTENTS. Editing filters or
        # thresholds in place does not change the resource path, so keying the
        # cache on the path alone would keep serving PASS verdicts reached under
        # the old filters. Bump this whenever the template changes.
        self._revision = revision

    @property
    def policy_version(self) -> str:
        """Path AND revision. Both, because only one of them ever changes."""
        return f"{self._template}@{self._revision}"

    @property
    def model_version(self) -> str:
        return self._model_version

    def _connect(self) -> Any:
        if self._client is not None:
            return self._client
        from google.api_core.client_options import ClientOptions
        from google.cloud import modelarmor_v1

        # Model Armor is REGIONAL. A default client talks to the global endpoint
        # and the template lookup fails, which this class would then report as
        # an outage: correct behaviour, permanently, for a reachable service.
        self._client = modelarmor_v1.ModelArmorClient(
            client_options=ClientOptions(
                api_endpoint=f"modelarmor.{self._location}.rep.googleapis.com"
            )
        )
        return self._client

    def _request(self, text: str) -> Any:
        """Built through the SDK, or as a plain mapping when it is absent.

        The import lives here rather than beside the call so an injected client
        can be exercised WITHOUT the SDK installed. Otherwise the outage tests
        pass on ImportError and never reach the fake at all, which is a test
        asserting that the import failed.
        """
        try:
            from google.cloud import modelarmor_v1
        except ImportError:
            return {"name": self._template, "user_prompt_data": {"text": text}}
        return modelarmor_v1.SanitizeUserPromptRequest(
            name=self._template,
            user_prompt_data=modelarmor_v1.DataItem(text=text),
        )

    def screen(self, text: str) -> tuple[ScreenVerdict, str | None]:
        """Return (verdict, category). Never raises, always fails closed."""
        encoded = text.encode("utf-8")
        if len(encoded) > MAX_TEXT_BYTES:
            return "BLOCK", str(ErrorIds.SCREEN_TEXT_TOO_LARGE)
        try:
            client = self._connect()
            response = client.sanitize_user_prompt(
                request=self._request(text), timeout=self._timeout
            )
        except Exception as exc:
            # Raised rather than returned so the gate can tell an outage from a
            # verdict and decline to cache it.
            raise ScreeningUnavailable(f"{type(exc).__name__}: {exc}") from exc

        return _interpret(response)


class ScreeningUnavailable(RuntimeError):
    """Model Armor could not be reached or did not answer usefully.

    Raised rather than returned as BLOCK so `ScreeningGate` can redact the text
    without caching the outcome. Caching an outage would keep redacting the text
    long after the service came back.
    """


def _interpret(response: Any) -> tuple[ScreenVerdict, str | None]:
    """Read the sanitize result, defaulting to BLOCK on anything unfamiliar.

    Model Armor reports a per-filter breakdown plus an overall match state. Only
    an explicit NO_MATCH_FOUND passes; every other value, including one this
    build does not recognise, blocks. A response shape that changed upstream must
    fail closed rather than read as clean.
    """
    result = getattr(response, "sanitization_result", None)
    if result is None:
        return "BLOCK", str(ErrorIds.SCREEN_UNAVAILABLE)

    # Checked BEFORE the match state, and this ordering is the whole point.
    # `invocation_result` reports whether the filters actually ran; PARTIAL and
    # FAILURE mean some were skipped or errored, and Model Armor still returns a
    # match state alongside them. A NO_MATCH_FOUND from a run where half the
    # filters never executed is "we did not check", and reading it as PASS is
    # this system's own cardinal error committed against itself.
    invocation = getattr(
        getattr(result, "sanitization_metadata", None), "error_code", None
    )
    ran = getattr(result, "invocation_result", None)
    ran_name = getattr(ran, "name", str(ran) if ran is not None else "")
    if ran_name and ran_name not in ("SUCCESS", "INVOCATION_RESULT_UNSPECIFIED"):
        return "BLOCK", f"{ErrorIds.SCREEN_UNAVAILABLE}: invocation {ran_name}"
    if invocation:
        return "BLOCK", f"{ErrorIds.SCREEN_UNAVAILABLE}: {invocation}"

    state = getattr(result, "filter_match_state", None)
    name = getattr(state, "name", str(state))
    if name == "NO_MATCH_FOUND":
        return "PASS", None
    if name == "MATCH_FOUND":
        return "BLOCK", _category(result)
    return "BLOCK", f"{ErrorIds.SCREEN_UNAVAILABLE}: unrecognised match state {name}"


def _category(result: Any) -> str:
    """Which filter matched, so an incident names the finding rather than a code."""
    matched = []
    for key, value in (getattr(result, "filter_results", None) or {}).items():
        state = getattr(
            getattr(value, "pi_and_jailbreak_filter_result", None),
            "match_state",
            None,
        )
        name = getattr(state, "name", "")
        if name == "MATCH_FOUND":
            matched.append(key)
            continue
        for attribute in (
            "sdp_filter_result",
            "rai_filter_result",
            # The API field carries the doubled word. Spelling it the obvious
            # way silently loses every CSAM block into "unspecified".
            "csam_filter_filter_result",
            "malicious_uri_filter_result",
            "virus_scan_filter_result",
        ):
            sub = getattr(value, attribute, None)
            if getattr(getattr(sub, "match_state", None), "name", "") == "MATCH_FOUND":
                matched.append(key)
                break
    return ",".join(sorted(matched)) if matched else "unspecified"
