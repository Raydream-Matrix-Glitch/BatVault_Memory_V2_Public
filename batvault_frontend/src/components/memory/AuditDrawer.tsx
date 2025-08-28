import React, { useState } from "react";
import Tab from "./ui/Tab";
import Button from "./ui/Button";
import type {
  MetaInfo,
  EvidenceBundle,
  WhyDecisionAnswer,
} from "../../types/memory";

export interface AuditDrawerProps {
  /**
   * Whether the drawer is visible. When false, the drawer is off‑screen.
   */
  open: boolean;
  /** Handler to close the drawer. */
  onClose: () => void;
  /** Metadata returned with the final response. */
  meta?: MetaInfo;
  /** Evidence bundle from the final response for listing allowed/dropped IDs. */
  evidence?: EvidenceBundle;
  /** Answer object for context (unused in this drawer for now). */
  answer?: WhyDecisionAnswer;
}

/**
 * AuditDrawer displays detailed audit information for a completed Memory API
 * response. It slides in from the right and provides several tabs: Trace,
 * Prompt, Evidence, Metrics and Fingerprints. Large JSON payloads are
 * collapsible by default and can be copied to the clipboard. Neon colours
 * highlight important values while preserving readability.
 */
const AuditDrawer: React.FC<AuditDrawerProps> = ({
  open,
  onClose,
  meta,
  evidence,
  answer,
}) => {
  const [activeTab, setActiveTab] = useState<
    "trace" | "prompt" | "evidence" | "metrics" | "fingerprints"
  >("trace");
  const [showEnvelope, setShowEnvelope] = useState(false);
  const [showRendered, setShowRendered] = useState(false);
  const [showRaw, setShowRaw] = useState(false);

  // Helper to copy text to clipboard and notify the user silently.
  const copyToClipboard = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      // Optionally we could show a toast/snackbar; omit for now to keep scope small.
    } catch {
      // ignore clipboard errors
    }
  };

  // Default trace stages when no trace is provided
  const defaultStages = [
    "resolve",
    "plan",
    "exec",
    "bundle",
    "prompt",
    "llm",
    "validate",
    "render",
    "stream",
  ];

  // Flatten allowed and dropped IDs for evidence tab
  const allowed = evidence?.allowed_ids ?? [];
  const dropped = meta?.dropped_evidence_ids ?? [];
  const selectorScores = meta?.selector_scores ?? {};

  // Determine classes for drawer visibility. Increase width to accommodate all tabs
  // and ensure it doesn't cut off the last tab. On small screens, it still slides
  // in from the right with a fixed width (~28rem).
  const drawerClasses = `fixed top-0 right-0 h-full w-[28rem] max-w-full bg-darkbg shadow-lg transform transition-transform duration-300 z-50 ${
    open ? "translate-x-0" : "translate-x-full"
  }`;

  return (
    <div className={drawerClasses} data-testid="audit-drawer">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
        <h2 className="text-xl font-bold text-vaultred">Audit</h2>
        <Button variant="secondary" onClick={onClose} className="px-2 py-1 text-sm">
          Close
        </Button>
      </div>
      {/* Tabs */}
      <div className="flex space-x-3 px-4 border-b border-gray-700 overflow-x-auto">
        <Tab active={activeTab === "trace"} onClick={() => setActiveTab("trace")}>Trace</Tab>
        <Tab active={activeTab === "prompt"} onClick={() => setActiveTab("prompt")}>Prompt</Tab>
        <Tab active={activeTab === "evidence"} onClick={() => setActiveTab("evidence")}>Evidence</Tab>
        <Tab active={activeTab === "metrics"} onClick={() => setActiveTab("metrics")}>Metrics</Tab>
        <Tab active={activeTab === "fingerprints"} onClick={() => setActiveTab("fingerprints")}>Fingerprints</Tab>
      </div>
      <div className="overflow-y-auto p-4 space-y-4" style={{ height: "calc(100% - 100px)" }}>
        {/* Trace tab */}
        {activeTab === "trace" && (
          <div>
            <h3 className="text-lg font-semibold text-vaultred mb-2">Gateway trace</h3>
            <ol className="list-decimal list-inside space-y-1 text-copy text-sm">
              {(meta?.trace && meta.trace.length > 0 ? meta.trace : defaultStages).map(
                (stage, idx) => (
                  <li key={`${stage}-${idx}`}>{stage}</li>
                )
              )}
            </ol>
          </div>
        )}
        {/* Prompt tab */}
        {activeTab === "prompt" && (
          <div className="space-y-4">
            <div>
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-semibold text-vaultred">Envelope</h3>
                <Button
                  variant="secondary"
                  onClick={() =>
                    meta?.prompt_envelope &&
                    copyToClipboard(JSON.stringify(meta.prompt_envelope, null, 2))
                  }
                  className="text-xs"
                >
                  Copy
                </Button>
              </div>
              <Button
                variant="secondary"
                onClick={() => setShowEnvelope((s) => !s)}
                className="my-1 text-xs"
              >
                {showEnvelope ? "Hide" : "Show"}
              </Button>
              {showEnvelope && !!meta?.prompt_envelope && (
                <pre className="bg-darkbg border border-gray-700 rounded p-2 text-xs overflow-x-auto whitespace-pre-wrap">
                  {JSON.stringify(meta.prompt_envelope, null, 2)}
                </pre>
              )}
            </div>
            <div>
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-semibold text-vaultred">Rendered prompt</h3>
                <Button
                  variant="secondary"
                  onClick={() =>
                    meta?.rendered_prompt && copyToClipboard(meta.rendered_prompt)
                  }
                  className="text-xs"
                >
                  Copy
                </Button>
              </div>
              <Button
                variant="secondary"
                onClick={() => setShowRendered((s) => !s)}
                className="my-1 text-xs"
              >
                {showRendered ? "Hide" : "Show"}
              </Button>
              {showRendered && meta?.rendered_prompt && (
                <pre className="bg-darkbg border border-gray-700 rounded p-2 text-xs overflow-x-auto whitespace-pre-wrap font-mono">
                  {meta.rendered_prompt}
                </pre>
              )}
            </div>
            <div>
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-semibold text-vaultred">Raw LLM JSON</h3>
                <Button
                  variant="secondary"
                  onClick={() =>
                    meta?.raw_llm_json &&
                    copyToClipboard(JSON.stringify(meta.raw_llm_json, null, 2))
                  }
                  className="text-xs"
                >
                  Copy
                </Button>
              </div>
              <Button
                variant="secondary"
                onClick={() => setShowRaw((s) => !s)}
                className="my-1 text-xs"
              >
                {showRaw ? "Hide" : "Show"}
              </Button>
              {showRaw && !!meta?.raw_llm_json && (
                <pre className="bg-darkbg border border-gray-700 rounded p-2 text-xs overflow-x-auto whitespace-pre-wrap">
                  {JSON.stringify(meta.raw_llm_json, null, 2)}
                </pre>
              )}
            </div>
          </div>
        )}
        {/* Evidence tab */}
        {activeTab === "evidence" && (
          <div>
            <h3 className="text-lg font-semibold text-vaultred mb-2">Evidence IDs</h3>
            <div className="text-copy text-sm mb-2">
              <span className="font-semibold">Allowed</span> ({allowed.length}):
            </div>
            {allowed.length > 0 ? (
              <ul className="list-disc list-inside text-xs text-copy space-y-1 mb-4">
                {allowed.map((id) => (
                  <li key={id}>{id}</li>
                ))}
              </ul>
            ) : (
              <p className="text-copy text-xs">None</p>
            )}
            {dropped.length > 0 && (
              <>
                <div className="text-copy text-sm mb-2">
                  <span className="font-semibold text-yellow-400">Dropped</span> ({dropped.length}):
                </div>
                <ul className="list-disc list-inside text-xs text-copy space-y-1 mb-4">
                  {dropped.map((id) => (
                    <li key={id}>{id}</li>
                  ))}
                </ul>
              </>
            )}
            {Object.keys(selectorScores).length > 0 && (
              <div>
                <h4 className="text-sm font-semibold text-vaultred mb-1">Selector scores</h4>
                <table className="text-xs w-full">
                  <thead>
                    <tr className="text-left">
                      <th className="pr-4 py-1">ID</th>
                      <th className="py-1">Score</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(selectorScores).map(([id, score]) => (
                      <tr key={id} className="border-t border-gray-700">
                        <td className="pr-4 py-1 break-all">{id}</td>
                        <td className="py-1">{score.toFixed(3)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
           {/* Cited evidence IDs from the short answer */}
            {answer?.supporting_ids && answer.supporting_ids.length > 0 && (
              <div className="mt-4">
                <h4 className="text-sm font-semibold text-vaultred mb-1">Cited in short answer</h4>
                <ul className="list-disc list-inside text-xs text-copy space-y-1">
                  {answer.supporting_ids.map((cid) => (
                    <li key={cid}>{cid}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
        {/* Metrics tab */}
        {activeTab === "metrics" && (
          <div className="space-y-2 text-sm text-copy">
            <div>
              <span className="font-semibold">Latency</span>: {meta?.latency_ms ?? "–"} ms
            </div>
            <div>
              <span className="font-semibold">Retries</span>: {meta?.retries ?? "–"}
            </div>
            <div>
              <span className="font-semibold">Routing confidence</span>: {meta?.routing_confidence !== undefined ? meta.routing_confidence.toFixed(3) : "–"}
            </div>
            {meta?.function_calls && meta.function_calls.length > 0 && (
              <div>
                <span className="font-semibold">Function calls</span>: {meta.function_calls.join(", ")}
              </div>
            )}
            {meta?.fallback_used !== undefined && (
              <div>
                <span className="font-semibold">Fallback used</span>: {meta.fallback_used ? "yes" : "no"}
              </div>
            )}
            {meta?.fallback_reason && (
              <div>
                <span className="font-semibold">Fallback reason</span>: {meta.fallback_reason}
              </div>
            )}
            {meta?.cache_hit !== undefined && (
              <div>
                <span className="font-semibold">Cache hit</span>: {meta.cache_hit ? "yes" : "no"}
              </div>
            )}
            {/* Evidence bundling metrics (M3/M5) */}
            {meta?.total_neighbors_found !== undefined && (
              <div>
                <span className="font-semibold">Total neighbors found</span>: {meta.total_neighbors_found}
              </div>
            )}
            {meta?.final_evidence_count !== undefined && (
              <div>
                <span className="font-semibold">Final evidence count</span>: {meta.final_evidence_count}
              </div>
            )}
            {meta?.selector_truncation !== undefined && (
              <div>
                <span className="font-semibold">Selector truncation</span>: {meta.selector_truncation ? "yes" : "no"}
              </div>
            )}
            {(meta?.bundle_size_bytes !== undefined || meta?.max_prompt_bytes !== undefined) && (
              <div>
                <span className="font-semibold">Bundle size</span>: {meta?.bundle_size_bytes ?? "–"} bytes
                {meta?.max_prompt_bytes !== undefined && <> &nbsp; <span className="font-semibold">Budget</span>: {meta.max_prompt_bytes} bytes</>}
                {meta?.bundle_size_bytes !== undefined && meta?.max_prompt_bytes !== undefined && (
                  <div className="mt-1 text-xs">
                    Usage: {Math.min(100, Math.round((meta.bundle_size_bytes / meta.max_prompt_bytes) * 100))}%
                  </div>
                )}
              </div>
            )}
            {/* Fallback completeness from current evidence object */}
            {evidence && (
              <div>
                <span className="font-semibold">Evidence count</span>: {evidence.events.length + 1}
              </div>
            )}
          </div>
       )}
        {/* Fingerprints tab */}
        {activeTab === "fingerprints" && (
          <div className="space-y-3 text-sm text-copy break-all">
            {/* Request ID row */}
            <div className="flex items-center">
              <span className="font-semibold mr-1">Request ID:</span>
              <span className="ml-1">{meta?.request_id ?? "–"}</span>
              {meta?.request_id && (
                <Button
                  variant="secondary"
                  onClick={() => copyToClipboard(meta.request_id!)}
                  className="ml-2 text-xs px-1 py-0.5"
                >
                  Copy
                </Button>
              )}
            </div>
            {/* Plan fingerprint row */}
            <div className="flex items-center">
              <span className="font-semibold mr-1">Plan fingerprint:</span>
              <span className="ml-1">{meta?.plan_fingerprint ?? "–"}</span>
              {meta?.plan_fingerprint && (
                <Button
                  variant="secondary"
                  onClick={() => copyToClipboard(meta.plan_fingerprint!)}
                  className="ml-2 text-xs px-1 py-0.5"
                >
                  Copy
                </Button>
              )}
            </div>
           
            {/* Prompt fingerprint row */}
            <div className="flex items-center">
              <span className="font-semibold mr-1">Prompt fingerprint:</span>
              <span className="ml-1">
                {meta?.prompt_fingerprint ?? meta?.prompt_envelope_fingerprint ?? "–"}
              </span>
              {(meta?.prompt_fingerprint || meta?.prompt_envelope_fingerprint) && (
                <Button
                  variant="secondary"
                  onClick={() =>
                    copyToClipboard(
                      (meta?.prompt_fingerprint ?? meta?.prompt_envelope_fingerprint)!
                    )
                  }
                  className="ml-2 text-xs px-1 py-0.5"
                >
                  Copy
                </Button>
              )}
            </div>
            {/* Snapshot etag row */}
            <div className="flex items-center">
              <span className="font-semibold mr-1">Snapshot etag:</span>
              <span className="ml-1">{meta?.snapshot_etag ?? "–"}</span>
              {meta?.snapshot_etag && (
                <Button
                  variant="secondary"
                  onClick={() => copyToClipboard(meta.snapshot_etag!)}
                  className="ml-2 text-xs px-1 py-0.5"
                >
                  Copy
                </Button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default AuditDrawer;