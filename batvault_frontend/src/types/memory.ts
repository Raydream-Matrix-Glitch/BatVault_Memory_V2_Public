/**
 * Type definitions for Memory API responses and SSE streaming events.
 * These interfaces reflect the WhyDecisionResponse@1 schema and
 * streaming token semantics described in the technical spec.
 */

export interface EvidenceItem {
  id: string;
  type?: 'EVENT' | 'DECISION' | 'TRANSITION';
  summary?: string;
  timestamp?: string;
  rationale?: string;
  snippet?: string;
  tags?: string[];
  based_on?: string[];
  decision_maker?: string;
  from?: string;
  to?: string;
  relation?: string;
  reason?: string;
}

export interface EvidenceBundle {
  anchor: EvidenceItem;
  events: EvidenceItem[];
  transitions?: {
    preceding?: EvidenceItem[];
    succeeding?: EvidenceItem[];
  };
  allowed_ids: string[];
}

export interface WhyDecisionAnswer {
  short_answer: string;
  supporting_ids: string[];
  rationale_note?: string;
}

export interface CompletenessFlags {
  has_preceding: boolean;
  has_succeeding: boolean;
  event_count: number;
}

export interface MetaInfo {
  policy_id: string;
  prompt_id: string;
  retries: number;
  latency_ms: number;
  function_calls?: string[];
  routing_confidence?: number;
  prompt_fingerprint?: string;
  snapshot_etag?: string;
  // Optional extended metrics (present on newer gateways)
  prompt_tokens?: number;
  evidence_tokens?: number;
  max_tokens?: number;
  selector_model_id?: string;
  // Stage timings (ms) keyed by stage name, e.g. { resolve: 12, plan: 5, ... }
  stage_timings?: Record<string, number>;
  // Evidence bundling metrics bag; shape varies by policy version
  evidence_metrics?: Record<string, any>;
  fallback_used?: boolean;
  fallback_reason?: string;
  request_id?: string;
  plan_fingerprint?: string;
  prompt_envelope_fingerprint?: string;
  selector_scores?: Record<string, number>;
  dropped_evidence_ids?: string[];
  trace?: string[];
  prompt_envelope?: unknown;
  rendered_prompt?: string;
  raw_llm_json?: unknown;
  cache_hit?: boolean;
  total_neighbors_found?: number;
  selector_truncation?: boolean;
  final_evidence_count?: number;
  bundle_size_bytes?: number;
  max_prompt_bytes?: number;
}



export interface WhyDecisionResponse {
  intent: string;
  evidence: EvidenceBundle;
  answer: WhyDecisionAnswer;
  completeness_flags: CompletenessFlags;
  meta: MetaInfo;
  bundle_url?: string;
}

/**
 * Shape of streaming events delivered by the Memory API. During streaming,
 * events may contain a single token, and the final event will contain
 * the complete response including answer and evidence.
 */
export interface StreamEvent {
  token?: string;
  final?: boolean;
  answer?: WhyDecisionAnswer;
  evidence?: EvidenceBundle;
  meta?: MetaInfo;
  error?: any;
}

/**
 * Schema field grouping returned by /v2/schema/fields. The key is a
 * semantic name and the value is an array of synonyms or field identifiers.
 */
export type SchemaFields = Record<string, string[]>;

/**
 * Single relationship entry returned by /v2/schema/rels.
 */
export interface SchemaRelation {
  from: string;
  to: string;
  relation: string;
}

export interface EvidenceItem {
  id: string;
  type?: 'EVENT' | 'DECISION' | 'TRANSITION';
  summary?: string;
  timestamp?: string;
  rationale?: string;
  snippet?: string;
  tags?: string[];
  based_on?: string[];
  decision_maker?: string;
  /**
   * Indicates that this evidence item has no linking context (no based_on or supported_by).
   * The backend may omit this flag; if undefined, the UI may derive orphan status
   * from missing based_on or other fields.
   */
  orphan?: boolean;

  /**
   * When present, this evidence item leads to the specified decisions or events.
   * Not used in this implementation but reserved for future relation graph features.
   */
  led_to?: string[];
}