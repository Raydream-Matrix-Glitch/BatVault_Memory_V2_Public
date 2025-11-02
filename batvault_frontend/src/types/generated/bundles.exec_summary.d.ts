/* eslint-disable */
/** AUTO-GENERATED from JSON Schema. DO NOT EDIT. */

/**
 * Canonical anchor '<domain>#<id>' where domain is slash-scoped lower-kebab per domain.json and id matches [a-z0-9._:-]+.
 */
export type AnchorString = string;
/**
 * Slash-scoped lower-kebab domain. One or more segments separated by '/', each '[a-z0-9]+(?:-[a-z0-9]+)*'.
 */
export type DomainString = string;
/**
 * Canonical anchor '<domain>#<id>' where domain is slash-scoped lower-kebab per domain.json and id matches [a-z0-9._:-]+.
 */
export type AnchorString1 = string;
export type EdgeOrientedForExecSummary = EdgeOrientedForExecSummary1 & EdgeOrientedForExecSummary2;
export type EdgeOrientedForExecSummary1 = {
  [k: string]: unknown;
} & {
  [k: string]: unknown;
};

export interface ExecSummaryResponseJson {
  schema_version: "v3";
  response: {
    anchor: AnchorString | DecisionNode | EventNode;
    graph: {
      edges: EdgeOrientedForExecSummary[];
    };
    meta: {
      allowed_ids: string[];
      fingerprints: {
        graph_fp: string;
      };
      allowed_ids_fp: string;
      policy_fp: string;
      snapshot_etag: string;
      bundle_fp: string;
      selector_policy_id?: string;
      budget_cfg_fp?: string;
    };
    answer: {
      blocks: StructuredAnswerBlocks;
      short_answer?: string;
      /**
       * @minItems 1
       */
      cited_ids?: [AnchorString, ...AnchorString[]];
    };
    completeness_flags: {
      has_preceding: boolean;
      has_succeeding: boolean;
      event_count?: number;
    };
  };
}
export interface DecisionNode {
  id: string;
  type: "DECISION";
  title: string;
  description?: string;
  decision_maker: {
    id: string;
    name?: string;
    role: string;
    org?: string;
  };
  domain: DomainString;
  timestamp: string;
  sensitivity?: "low" | "medium" | "high";
  "x-extra"?: {};
}
export interface EventNode {
  id: string;
  type: "EVENT";
  title: string;
  description?: string;
  domain: DomainString;
  timestamp: string;
  sensitivity?: "low" | "medium" | "high";
  decision_ref?: AnchorString1;
  "x-extra"?: {};
}
export interface EdgeOrientedForExecSummary2 {
  type: "LED_TO" | "CAUSAL" | "ALIAS_OF";
  from: AnchorString;
  to: AnchorString;
  timestamp: string;
  domain?: DomainString;
  orientation?: "preceding" | "succeeding";
}
export interface StructuredAnswerBlocks {
  lead: string;
  description?: string;
  /**
   * @minItems 1
   */
  key_events?: [string, ...string[]];
  next?: string;
  owner?: {
    name: string;
    role?: string;
  };
  decision_id?: string;
}
