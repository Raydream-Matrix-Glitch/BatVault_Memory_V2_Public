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

export interface MemoryGraphViewReadTimeV3SnapshotBound {
  anchor: MaskedNode;
  graph: {
    edges: EdgeWire[];
  };
  meta: MemoryMetaV3SnapshotBound;
}
export interface MaskedNode {
  id: AnchorString;
  type: "DECISION" | "EVENT";
  domain: DomainString;
  timestamp?: string;
  title?: string;
  description?: string;
  sensitivity?: "low" | "medium" | "high" | "internal" | "secret";
  decision_maker?: {
    id?: string;
    role?: string;
    name?: string;
    org?: string;
  };
  /**
   * Event→Decision anchor reference when the anchor is an EVENT
   */
  decision_ref?: string;
  /**
   * @minItems 1
   */
  kinds?: ["EVENT" | "DECISION", ...("EVENT" | "DECISION")[]];
  "x-extra"?: {};
}
export interface EdgeWire {
  type: "LED_TO" | "CAUSAL" | "ALIAS_OF";
  from: AnchorString;
  to: AnchorString;
  timestamp: string;
  domain?: DomainString;
}
export interface MemoryMetaV3SnapshotBound {
  returned_count: number;
  allowed_ids: string[];
  allowed_ids_fp: string;
  policy_fp: string;
  snapshot_etag: string;
  fingerprints: {
    graph_fp: string;
  };
  alias: {
    partial: boolean;
    max_depth: 1;
    returned: string[];
  };
}
