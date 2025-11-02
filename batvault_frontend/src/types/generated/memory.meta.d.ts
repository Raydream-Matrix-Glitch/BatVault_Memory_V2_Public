/* eslint-disable */
/** AUTO-GENERATED from JSON Schema. DO NOT EDIT. */

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
