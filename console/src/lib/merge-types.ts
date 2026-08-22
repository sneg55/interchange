/**
 * The records the reconciler and the republisher write. Spec section 7.
 *
 * Split from `types.ts` when that file outgrew its size budget. The boundary is
 * the one already implicit in the file: everything here describes a canonical
 * zone or a republish cycle, and everything left behind describes one
 * publisher's trust history. `types.ts` re-exports all of it, so callers import
 * from either without caring which.
 */

import type { FleetState } from './types'

export type MergeTier = 'TIER_1_DETERMINISTIC' | 'TIER_2_ADJUDICATED' | 'SINGLETON'

export interface SourceRef {
  publisher_key: string
  road_event_id: string
  data_source_id: string | null
  trust_state: FleetState
  ingested_at: string
  source_update_date: string | null
  distance_m: number | null
  coverage: number | null
  merge_tier: MergeTier
}

export interface DroppedEdge {
  publisher_key: string
  road_event_id: string
  other_publisher_key: string
  other_road_event_id: string
  distance_m: number | null
}

export interface ConflictRecord {
  type: 'FIELD_DISAGREEMENT' | 'AMBIGUOUS_GROUPING'
  detected_at: string
  field: string | null
  values: { publisher_key: string; value: unknown; update_date: string }[]
  emitted_value: unknown
  resolution: 'MOST_RECENT_UPDATE_DATE' | 'EDGE_DROPPED' | null
  dropped_edge: DroppedEdge | null
}

export interface CanonicalZone {
  canonical_id: string
  geometry: { type: string; coordinates: unknown } | null
  core_details: Record<string, unknown>
  start_date: string | null
  end_date: string | null
  sources: SourceRef[]
  conflicts: ConflictRecord[]
  supersedes: string[]
  bbox: [number, number, number, number] | null
}

export interface OutputArtifact {
  cycle_id: string
  at: string
  feed_uri: string | null
  /**
   * Every canonical zone the merge handed the republisher, before exclusions.
   *
   * Optional because artifacts written before this field existed do not carry
   * it. Without it the output screen printed three counts a reader could not
   * reconcile: zones published, zones missing a required field and zones failing
   * validation add to this number, and nothing on screen said so.
   */
  input_zone_count?: number
  canonical_zone_count: number
  /** Source zones behind the EMITTED zones, not the cycle's input. */
  source_zone_count: number
  validation_result: {
    schema_version: string
    error_count: number | null
    unresolvable: boolean
    errors: string[]
    errors_truncated: boolean
  }
  published: boolean
  excluded_counts: Record<string, number>
  excluded_zone_ids: Record<string, string[]>
  /**
   * Source zones a quarantined publisher never contributed, by publisher key.
   *
   * Distinct from `excluded_counts.quarantined_sources_only`, and the two are
   * not interchangeable. Quarantined publishers are held back BEFORE the merge,
   * so the republisher's own counter can only ever report zero: it counts among
   * zones it received. The console showed that zero as the whole account of what
   * quarantine excluded, while 824 zones had in fact been withheld.
   *
   * Optional because artifacts written before this field existed genuinely do
   * not carry it, and those documents are still in the collection. A required
   * field here would be the type asserting something about stored data that is
   * not true, and the reader would treat a missing value as zero withheld:
   * "not recorded" read as "nothing happened", which is the error this whole
   * field exists to correct.
   */
  withheld_source_zones?: Record<string, number>
  withheld_source_zone_count?: number
  /**
   * Why each withheld publisher was held back: QUARANTINE, NOT_POLLABLE, or
   * NO_RETAINED_BODY. The third is not a verdict about the publisher. It is
   * trusted and simply contributed nothing this cycle, because its poll failed
   * or it answered 304 with nothing retained to answer it with, and its count is
   * what its last measured poll counted rather than a measurement of this one.
   */
  withheld_reasons?: Record<string, string>
  /** Which required fields were missing, and from how many zones. */
  missing_field_counts?: Record<string, number>
}

/** Spec 7. One per republish cycle: the merge's own account of what it did. */
export interface ReconciliationSnapshot {
  cycle_id: string
  at: string
  group_count: number
  conflict_count: number
  /** Groups with more than one source. `group_count` includes singletons. */
  merged_zone_count: number
  tier_counts: Record<string, number>
  excluded_counts: Record<string, number>
  adjudication_counts: Record<string, number>
  /** A bounded sample of the pairs symmetric coverage refused. */
  rejected_pairs: RejectedPair[]
  /** How many were refused in total, which is not how many are in the sample. */
  rejected_pair_total: number
}

/** Spec 6.6's negative control, as measured rather than as asserted. */
export interface RejectedPair {
  left_publisher: string
  left_road_event_id: string
  right_publisher: string
  right_road_event_id: string
  distance_m: number | null
  coverage: number | null
}

/** Spec 7. Append-only, and what makes fleet membership replayable. */
export interface RegistryEventDoc {
  publisher_key: string
  at: string
  event:
    | 'PROVISIONED'
    | 'URL_CHANGED'
    | 'CADENCE_CHANGED'
    | 'VERSION_CHANGED'
    | 'ABSENT'
    | 'DECOMMISSIONED'
    | 'REAPPEARED'
  from_value: unknown
  to_value: unknown
}
