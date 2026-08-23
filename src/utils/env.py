"""Single env boundary. See guides/zod-at-the-boundary.md (Python section).

Rules:
  1. This file is the ONLY place environment variables are read.
     (Grep for `os.environ` in review; nothing outside this file may use it.)
  2. The Env model is the source of truth for the config type.
  3. Validation happens at import time - fail fast on misconfiguration.
  4. Add new vars here, declare their shape, provide a default where sensible.

Consumers:
    from src.utils.env import env
    poll_in = max(declared_seconds, env.poll_floor_seconds)

Requires: pydantic >= 2, pydantic-settings.
"""

from __future__ import annotations

import sys
from typing import Literal

from pydantic import Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Env(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Runtime ──────────────────────────────────────────────────────────────
    app_env: Literal["development", "test", "production"] = "development"
    port: int = Field(default=8080, gt=0)
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    # ── Google Cloud ─────────────────────────────────────────────────────────
    gcp_project_id: str = ""
    gcp_region: str = "us-central1"
    firestore_database: str = "(default)"
    gcs_bucket_snapshots: str = ""
    gcs_bucket_output: str = ""

    # ── Gemini ───────────────────────────────────────────────────────────────
    # Used ONLY for Tier 2 duplicate adjudication and notice drafting. Never in
    # the trust gate path.
    #
    # Authenticated through Vertex AI with the runner's own service account, so
    # there is no API key here and none on the box: `genai.Client()` reads
    # GOOGLE_GENAI_USE_VERTEXAI, GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION
    # from the process environment itself. That is the SDK reading its own
    # configuration, the same way the Firestore client reads its credentials, and
    # not a second env boundary: nothing in this repository reads them.
    #
    # A `gemini_api_key` and an `adjudication_prompt_version` used to sit here.
    # Nothing read either one. The prompt version is `PROMPT_VERSION` in
    # `src/services/gemini.py`, beside the prompt it versions.
    #
    # Flash rather than Pro, matching `DEFAULT_MODEL` in that module. Tier 2 asks
    # for one word against a response schema and the notice drafter writes two
    # paragraphs from facts it is forbidden to add to; neither is reasoning-bound,
    # and this runs against every ambiguous pair in a cycle.
    #
    # The 3.x models are served only from the `global` location. Pointing at one
    # while GOOGLE_CLOUD_LOCATION is a region gives 404 on every call, which the
    # adjudicator would record as UNSURE rather than as an outage.
    gemini_model: str = "gemini-3.5-flash"

    # ── Model Armor ──────────────────────────────────────────────────────────
    # Screening fails closed. An unset template is a valid production state and
    # means every free-text field is redacted, never that screening is skipped.
    #
    # Model Armor is regional and addressed by template resource path, not by a
    # URL: the screener composes `projects/{p}/locations/{l}/templates/{t}` from
    # gcp_project_id, gcp_region and the id below. An `endpoint` field lived here
    # for a while and nothing could consume it, which left the integration
    # unreachable while the config looked complete.
    model_armor_template_id: str = ""
    # Labels the template's CONTENTS, not its path. Editing filters in place does
    # not change the path, so a cache keyed on the path alone would keep serving
    # verdicts reached under the old filters. Bump on every template edit.
    model_armor_policy_version: str = "r1"

    # ── Poll scheduling (SPEC 6.3) ───────────────────────────────────────────
    # The floor is load-bearing: honouring declared cadences literally measured
    # 23.2 GB/day of ingress, and the fleet has to run for weeks.
    poll_floor_seconds: int = Field(default=300, gt=0)
    poll_ceiling_seconds: int = Field(default=3600, gt=0)

    # The trust scorer's ruleset version is deliberately NOT here. It is
    # `rules.RULESET_VERSION`, and it belongs beside the thresholds it names so
    # that changing one without the other is visible in a single diff. Declared
    # in both places it drifted: the env copy sat at "v1" unread while the rules
    # moved, and a config value nothing consults is worse than no config at all.

    # ── Reconciler (SPEC 6.6) ────────────────────────────────────────────────
    match_threshold_metres: float = Field(default=150.0, gt=0)
    min_symmetric_coverage: float = Field(default=0.6, ge=0, le=1)

    @model_validator(mode="after")
    def _check_poll_window(self) -> Env:
        if self.poll_ceiling_seconds < self.poll_floor_seconds:
            raise ValueError(
                "poll_ceiling_seconds must be >= poll_floor_seconds; "
                f"got {self.poll_ceiling_seconds} < {self.poll_floor_seconds}"
            )
        return self


def _load_env() -> Env:
    try:
        return Env()
    except ValidationError as e:
        # Render a readable error at startup. One bad env var should surface the
        # exact field and reason, not crash 10 stack frames deep.
        lines = [f"  {'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
        print(  # noqa: T201 - startup error; must reach stderr.
            "[env] invalid configuration:\n" + "\n".join(lines),
            file=sys.stderr,
        )
        raise SystemExit(1) from e


env = _load_env()
