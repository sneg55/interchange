/**
 * The records the console reads. Spec section 7.
 *
 * Hand-written rather than generated, because the Python side is the authority
 * and a generator would invite the two to drift silently under a green build.
 * These are read-only projections: the console never constructs a record it
 * does not own, and the only thing it writes is an approval decision.
 */

export type FleetState = 'ADMIT' | 'WATCH' | 'QUARANTINE' | 'NO_ACCESS'
export type ChurnStatus = 'OK' | 'INSUFFICIENT_HISTORY'
export type ApprovalState = 'DRAFT' | 'APPROVED' | 'WITHHELD'

/**
 * A rule verdict. NOT_APPLICABLE is a first-class outcome and the console must
 * render it distinctly from ADMIT: "we did not check" and "we checked and it
 * passed" look identical to a viewer who is shown only a green tick.
 */
export type Verdict = 'NOT_APPLICABLE' | 'ADMIT' | 'WATCH' | 'QUARANTINE'

/** Why a rule returned what it returned. Spec 6.4. */
export type Reason =
  | 'EVALUATED'
  | 'MEASURED_INAPPLICABLE'
  | 'NO_BODY'
  | 'MISSING_INPUT'
  | 'SCHEMA_UNKNOWN'
  | 'SUPPRESSED'
  | 'INSUFFICIENT_HISTORY'

/**
 * R5's own measurement, carried onto the record so a screen can show it.
 *
 * The scorer had these figures on every poll and nothing wrote them down, so the
 * console could say only that churn had been measured. The four fields are R5's
 * `detail` verbatim: how many polls fell inside the window, how many of those
 * advanced the publisher's own timestamp, how many walked it backwards, and how
 * long a span the compared run covers.
 */
export interface ChurnDetail {
  polls_in_window: number
  advances: number
  regressions: number
  span_seconds: number
}

export interface PublisherRecord {
  publisher_key: string
  org: string
  feedname: string
  us_state: string | null
  registered_since: string | null
  url: string
  declared_version: string | null
  declared_cadence_seconds: number
  needs_api_key: boolean
  fleet_state: FleetState
  state_before_no_access: FleetState | null
  churn_status: ChurnStatus
  /**
   * What R5 measured on the last poll it could evaluate.
   *
   * Optional because records written before this field existed genuinely do not
   * carry it, and a required field would have the type asserting something about
   * stored data that is not true. A reader must render a missing value as "not
   * recorded" rather than as a measured zero.
   */
  churn_detail?: ChurnDetail | null
  ruleset_version: string
  latching_rule_ids: string[]
  clean_poll_streak: number
  clean_streak_started_at: string | null
  first_seen: string
  last_seen_in_registry: string
  absent_pull_count: number
  decommissioned_at: string | null
  agent_identity: string | null
  poll_interval_seconds: number
  /**
   * When this publisher was last actually polled. Null means never.
   *
   * Never must render as "never", not as a blank and not as "just now". A
   * NO_ACCESS publisher has genuinely never been contacted, and that is a
   * governance fact worth reading rather than a gap to tidy away.
   */
  last_polled_at: string | null
}

export interface Observation {
  publisher_key: string
  polled_at: string
  http_status: number
  /**
   * Round-trip time, or null when the poll never completed.
   *
   * Null rather than zero. Zero milliseconds is the best possible latency, and
   * every failed poll rendered it: the console printed `0ms` beside four columns
   * showing an absence marker for the same poll, while the daily rollup on the
   * same page reported the latency as never measured. Recording "we did not
   * measure" as a measured best case is this system's cardinal error committed
   * in its own records.
   */
  latency_ms: number | null
  not_modified: boolean
  carried_forward: boolean
  etag: string | null
  last_modified: string | null
  update_date: string | null
  update_age_seconds: number | null
  feature_count: number | null
  active_count: number | null
  active_with_past_end_date: number | null
  active_undated: number | null
  schema_version_used: string | null
  schema_error_count: number | null
  content_hash: string | null
  structural_hash: string | null
  body_uri: string | null
  trace_id: string | null
  error: string | null
}

export interface RuleResult {
  rule_id: string
  verdict: Verdict
  reason: Reason
  detail: Record<string, number | string>
}

export interface RuleEvaluation {
  publisher_key: string
  observation_id: string
  evaluated_at: string
  ruleset_version: string
  results: RuleResult[]
  instantaneous_verdict: Verdict
  resulting_state: FleetState
  clean: boolean
}

export interface TrustTransition {
  publisher_key: string
  at: string
  from_state: FleetState
  to_state: FleetState
  rule_ids: string[]
  primary_rule_id: string | null
  direction: 'ESCALATION' | 'DE_ESCALATION'
  ruleset_version: string
  observation_ids: string[]
  evidence_packet_id: string | null
}

export interface PublisherDaily {
  publisher_key: string
  day: string
  poll_count: number
  failure_count: number
  not_modified_count: number
  latency_p50_ms: number | null
  latency_p95_ms: number | null
  max_update_age_seconds: number | null
  schema_error_count: number | null
  content_hash_changes: number
  fired_rules: string[]
  end_of_day_state: FleetState
}

export interface EvidencePacket {
  packet_id: string
  publisher_keys: string[]
  finding_type: string
  created_at: string
  rule_ids: string[]
  ruleset_version: string
  observation_window: { start: string; end: string; count: number }
  observations: Observation[]
  observations_truncated: boolean
  total_observations: number
  consumer_rendering: string | null
  registry_rendering: string | null
  approval_state: ApprovalState
  approved_by: string | null
  approved_at: string | null
  approved_rendering_sha256: string | null
  resolved_at: string | null
}

// The merge and output records live in `merge-types.ts`. Split when this file
// outgrew its size budget; the boundary is the one that was already there, since
// everything below described a canonical zone or a republish cycle rather than a
// publisher's trust history.
export type {
  CanonicalZone,
  ConflictRecord,
  DroppedEdge,
  MergeTier,
  OutputArtifact,
  ReconciliationSnapshot,
  RegistryEventDoc,
  RejectedPair,
  SourceRef,
} from './merge-types'
