// Barrel file — re-export generated types (v3) without name collisions.
// Prefer module namespaces over flat exports to avoid duplicate symbol errors.
// Canonical top-level alias is provided where we know teams import the symbol directly.

export * as GraphView from './generated/memory.graph_view';
export * as ExecSummary from './generated/bundles.exec_summary';
export * as MemoryMeta from './generated/memory.meta';
export * as BundlesView from './generated/bundles.view';
export * as BundlesTrace from './generated/bundles.trace';
export * as BundleManifest from './generated/bundle.manifest';
export * as Receipt from './generated/receipt';
export * as EdgeWire from './generated/edge.wire';
export * as GatewayPlan from './generated/gateway.plan';
export * as PolicyInput from './generated/policy.input';
export * as PolicyDecision from './generated/policy.decision';
export * as MemoryQuery from './generated/memory.query.request';
export * as MemoryResolve from './generated/memory.resolve.response';
export * as MetaInputs from './generated/meta.inputs';

// Back-compat: keep the canonical MemoryMetaV3SnapshotBound at top level
// and provide an explicit alias for the GraphView homonym to avoid ambiguity.
export type { MemoryMetaV3SnapshotBound } from './generated/memory.meta';
export type {
  MemoryMetaV3SnapshotBound as GraphViewMemoryMetaV3SnapshotBound
} from './generated/memory.graph_view';

// ---- Stable UI-facing aliases ---------------------------------------------
// Oriented edge as the FE consumes it (Gateway-oriented; Memory does not orient).
export type GraphEdge = {
  id: string;
  from: string;
  to: string;
  type: "CAUSAL" | "LED_TO" | "ALIAS_OF";
  domain?: string | null;
  /** Present for CAUSAL/LED_TO relative to the anchor; absent for ALIAS_OF. */
  orientation?: "preceding" | "succeeding";
};

// Minimal enriched node shape used by drawers/legends (masked titles/types).
export type EnrichedNode = {
  id: string;
  title?: string;
  description?: string;
  type?: "EVENT" | "DECISION";
  kind?: "EVENT" | "DECISION";
  timestamp?: string;
  domain?: string;
};