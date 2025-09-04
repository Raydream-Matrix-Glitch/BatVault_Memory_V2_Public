import React, { useState, useEffect, useCallback } from "react";
import Tab from "./ui/Tab";
import Button from "./ui/Button";
import { logEvent } from "../../utils/logger";
// Import a handful of FontAwesome icons to visually reinforce each audit tab.
import { FaStream, FaFileAlt, FaDatabase, FaChartBar, FaFingerprint } from 'react-icons/fa';
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
  bundle_url?: string;
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
  bundle_url,
}) => {

  // Lazy prompt artifacts loaded from the bundle when not present in meta
  const [bundlePrompt, setBundlePrompt] = useState<{ envelope?: any; rendered?: string; raw?: any } | null>(null);
  const [bundleLoading, setBundleLoading] = useState(false);
  const effectivePromptEnvelope = meta?.prompt_envelope ?? bundlePrompt?.envelope;
  const effectiveRenderedPrompt = meta?.rendered_prompt ?? bundlePrompt?.rendered;
  const effectiveRawLLM        = meta?.raw_llm_json     ?? bundlePrompt?.raw;
  const loadPromptFromBundle = useCallback(async () => {
    if (!meta?.request_id) return;
    setBundleLoading(true);
    try { logEvent("ui.audit.load_bundle", { rid: meta.request_id, kind: "prompt_artifacts" }); } catch {}
    try {
      const resp = await fetch(`/v2/bundles/${meta.request_id}`);
      if (!resp.ok) throw new Error(`bundle get failed: ${resp.status}`);
      const data = await resp.json().catch(() => null) as any;
      if (data) {
        // Prefer new gateway keys; fall back to legacy names to be safe.
        const envStr =
          data["envelope.json"] ??
          data["prompt_envelope.json"] ??
          undefined;
        const env = envStr ? JSON.parse(envStr) : undefined;
        const rend = data["rendered_prompt.txt"] ?? undefined;
        const rawStr =
          data["llm_raw.json"] ??
          data["raw_llm.json"] ??
          undefined;
        const raw = rawStr ? JSON.parse(rawStr) : undefined;
        setBundlePrompt({ envelope: env, rendered: rend, raw });
        try { logEvent("ui.audit.load_bundle.ok", { rid: meta.request_id }); } catch {}
      }
    } catch (e: any) {
      try { logEvent("ui.audit.load_bundle.err", { rid: meta?.request_id ?? null, message: String(e?.message || e) }); } catch {}
    } finally {
      setBundleLoading(false);
    }
  }, [meta?.request_id]);
  const [activeTab, setActiveTab] = useState<
    "trace" | "prompt" | "evidence" | "metrics" | "fingerprints"
  >("trace");
  const [showEnvelope, setShowEnvelope] = useState(false);
  const [showRendered, setShowRendered] = useState(false);
  const [showRaw, setShowRaw] = useState(false);

  useEffect(() => {
    try {
      logEvent("ui.audit_tab_changed", { tab: activeTab, rid: meta?.request_id ?? null });
    } catch {
      /* ignore logging errors */
    }
  }, [activeTab, meta?.request_id]);

  useEffect(() => {
    if (activeTab === "trace") {
      try {
        logEvent("ui.audit.trace_render", {
          rid: meta?.request_id ?? null,
          stages: (meta?.trace && meta.trace.length) || defaultStages.length,
        });
      } catch {}
    }
  }, [activeTab, meta?.trace]);

  useEffect(() => {
    try {
      const el = document.querySelector('[data-testid="audit-drawer"]') as HTMLElement | null;
      if (!el) return;
      const box = el.getBoundingClientRect();
      logEvent("ui.audit.drawer_mount", {
        rid: meta?.request_id ?? null,
        top: box.top,
        bottom: box.bottom,
        height: box.height,
      });
    } catch {}
  }, []);

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
  const defaultStages = ["resolve", "plan", "exec_graph", "enrich", "bundle", "prompt", "llm", "validate", "render", "stream"];

  // Optional per-stage timings in ms (UI degrades gracefully if absent)
  const stageTimings = (meta as any)?.stage_timings ?? (meta as any)?.evidence_metrics?.stage_timings ?? null;
  // Flatten allowed and dropped IDs for evidence tab
  const allowed = evidence?.allowed_ids ?? [];
  // Prefer top-level fields; fall back to evidence_metrics (what the gateway emits today)
  const dropped = meta?.dropped_evidence_ids ?? (meta as any)?.evidence_metrics?.dropped_evidence_ids ?? [];
  const selectorScores = meta?.selector_scores ?? (meta as any)?.evidence_metrics?.selector_scores ?? {};
  const preceding = (evidence as any)?.transitions?.preceding ?? [];
  const succeeding = (evidence as any)?.transitions?.succeeding ?? [];

  // Determine classes for drawer visibility. Increase width to accommodate all tabs
  // and ensure it doesn't cut off the last tab. On small screens, it still slides
  // in from the right with a fixed width (~28rem).
  const drawerClasses = `fixed top-0 bottom-0 right-0 m-0 h-screen w-[32rem] max-w-[96vw] bg-black/80 backdrop-blur-md border-l border-vaultred/40 shadow-neon-red transform transition-transform duration-300 z-50 ${
    open ? "translate-x-0" : "translate-x-full"
  }`;

  return (
    <div className={drawerClasses + " flex flex-col"} data-testid="audit-drawer">
      <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
        <h2 className="text-xl font-bold text-vaultred">Audit</h2>
        <div className="flex items-center gap-2">
          {meta?.request_id && (
            <Button
              variant="secondary"
              onClick={() => {
                try { logEvent("ui.audit.open_bundle", { rid: meta.request_id }); } catch {}
                const url = bundle_url || `/v2/bundles/${meta.request_id}`;
                window.open(url, "_blank");
              }}
              className="px-2 py-1 text-sm"
            >
              Open bundle
            </Button>
          )}
          <Button variant="secondary" onClick={onClose} className="px-2 py-1 text-sm">
            Close
          </Button>
        </div>
      </div>
      {/* Tabs */}
      <div className="flex space-x-3 px-4 border-b border-gray-700 overflow-x-auto">
        <Tab
          active={activeTab === "trace"}
          onClick={() => setActiveTab("trace")}
          className="flex items-center gap-1"
        >
          <FaStream className="w-3 h-3" />
          <span>Trace</span>
        </Tab>
        <Tab
          active={activeTab === "prompt"}
          onClick={() => setActiveTab("prompt")}
          className="flex items-center gap-1"
        >
          <FaFileAlt className="w-3 h-3" />
          <span>Prompt</span>
        </Tab>
        <Tab
          active={activeTab === "evidence"}
          onClick={() => setActiveTab("evidence")}
          className="flex items-center gap-1"
        >
          <FaDatabase className="w-3 h-3" />
          <span>Evidence</span>
        </Tab>
        <Tab
          active={activeTab === "metrics"}
          onClick={() => setActiveTab("metrics")}
          className="flex items-center gap-1"
        >
          <FaChartBar className="w-3 h-3" />
          <span>Metrics</span>
        </Tab>
        <Tab
          active={activeTab === "fingerprints"}
          onClick={() => setActiveTab("fingerprints")}
          className="flex items-center gap-1"
        >
          <FaFingerprint className="w-3 h-3" />
          <span>Fingerprint</span>
        </Tab>
      </div>
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Trace tab */}
        {activeTab === "trace" && (
          <div>
            <h3 className="text-lg font-semibold text-vaultred mb-2">Gateway trace</h3>
            <div className="space-y-2">
              {((meta?.trace && meta.trace.length > 0) ? meta.trace : defaultStages).map((stage, idx) => {
                const ms = stageTimings && (stageTimings as any)[stage];
                return (
                  <div
                    key={stage + "-" + idx}
                    className="w-full rounded-xl border border-vaultred/50 bg-black/40 px-3 py-2 text-sm text-copy flex items-center justify-between"
                  >
                    <div className="flex items-center">
                      <span className="font-mono opacity-70">{String(idx + 1).padStart(2, "0")}</span>
                      <span className="ml-3 font-semibold">{stage}</span>
                    </div>
                    {typeof ms === "number" && (
                      <span className="opacity-80 font-mono">{ms} ms</span>
                    )}
                  </div>
                );
              })}
            </div>
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
                  onClick={() => effectivePromptEnvelope && copyToClipboard(JSON.stringify(effectivePromptEnvelope, null, 2))}
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
              {!effectivePromptEnvelope && meta?.request_id && (
                <Button variant="secondary" onClick={loadPromptFromBundle} className="text-xs" disabled={bundleLoading}>
                  {bundleLoading ? "Loading…" : "Load from bundle"}
                </Button>
              )}
              {showEnvelope && !!effectivePromptEnvelope && (
                <pre className="bg-darkbg border border-gray-700 rounded p-2 text-xs overflow-x-auto whitespace-pre-wrap">
                  {JSON.stringify(effectivePromptEnvelope, null, 2)}
                </pre>
              )}
            </div>
            <div>
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-semibold text-vaultred">Rendered prompt</h3>
                <Button
                  variant="secondary"
                  onClick={() => effectiveRenderedPrompt && copyToClipboard(effectiveRenderedPrompt as any)}
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
              {!effectiveRenderedPrompt && meta?.request_id && (
                <Button variant="secondary" onClick={loadPromptFromBundle} className="text-xs" disabled={bundleLoading}>
                  {bundleLoading ? "Loading…" : "Load from bundle"}
                </Button>
              )}
              {showRendered && !!effectiveRenderedPrompt && (
                <pre className="bg-darkbg border border-gray-700 rounded p-2 text-xs overflow-x-auto whitespace-pre-wrap font-mono">
                  {effectiveRenderedPrompt as any}
                </pre>
              )}
            </div>
            <div>
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-semibold text-vaultred">Raw LLM JSON</h3>
                <Button
                  variant="secondary"
                  onClick={() =>
                    effectiveRawLLM &&
                    copyToClipboard(JSON.stringify(effectiveRawLLM, null, 2))
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
              {!effectiveRawLLM && meta?.request_id && (
                <Button variant="secondary" onClick={loadPromptFromBundle} className="text-xs" disabled={bundleLoading}>
                  {bundleLoading ? "Loading…" : "Load from bundle"}
                </Button>
              )}
              {showRaw && !!effectiveRawLLM && (
                <pre className="bg-darkbg border border-gray-700 rounded p-2 text-xs overflow-x-auto whitespace-pre-wrap">
                  {JSON.stringify(effectiveRawLLM, null, 2)}
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
                  <li key={id} className="font-mono break-all">{id}</li>
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
                    <li key={id} className="font-mono break-all">{id}</li>
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
                        <td className="pr-4 py-1 break-all font-mono">{id}</td>
                        <td className="py-1 font-mono">{score.toFixed(3)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {/* Transitions (preceding/succeeding) */}
            {(preceding.length > 0 || succeeding.length > 0) && (
              <div className="mt-4">
                <h4 className="text-sm font-semibold text-vaultred mb-1">Transitions</h4>
                <div className="grid grid-cols-2 gap-4 text-xs">
                  <div>
                    <div className="font-semibold mb-1">Preceding ({preceding.length})</div>
                    {preceding.length ? (
                      <ul className="list-disc list-inside space-y-1">
                        {preceding.map((t: any) => (
                          <li key={t.id} className="font-mono break-all">{t.id}</li>
                        ))}
                      </ul>
                    ) : <div className="opacity-70">None</div>}
                  </div>
                  <div>
                    <div className="font-semibold mb-1">Succeeding ({succeeding.length})</div>
                    {succeeding.length ? (
                      <ul className="list-disc list-inside space-y-1">
                        {succeeding.map((t: any) => (
                          <li key={t.id} className="font-mono break-all">{t.id}</li>
                        ))}
                      </ul>
                    ) : <div className="opacity-70">None</div>}
                  </div>
                </div>
              </div>
            )}
            {/* Cited evidence IDs from the short answer */}
            {answer?.supporting_ids && answer.supporting_ids.length > 0 && (
              <div className="mt-4">
                <h4 className="text-sm font-semibold text-vaultred mb-1">Cited in short answer</h4>
                <ul className="list-disc list-inside text-xs text-copy space-y-1">
                  {answer.supporting_ids.map((cid) => (
                    <li key={cid} className="font-mono break-all">{cid}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
        {/* Metrics tab */}
        {activeTab === "metrics" && (
          <div className="flex flex-col gap-2 text-sm text-copy">
            {[
              ["Latency", `${meta?.latency_ms ?? "–"} ms`],
              ["Retries", meta?.retries ?? "–"],
              ["Fallback used", meta?.fallback_used ? "yes" : "no"],
              ["Fallback reason", meta?.fallback_reason ?? "–"],
              ["Bundle size", (meta as any)?.bundle_size_bytes ?? "–"],
              ["Evidence count", evidence ? (evidence.events?.length ?? 0) + 1 : "–"],
              ["Prompt tokens", (meta as any)?.prompt_tokens ?? "–"],
              ["Evidence tokens", (meta as any)?.evidence_tokens ?? "–"],
              ["Max tokens", (meta as any)?.max_tokens ?? "–"],
              ["Selector", (meta as any)?.selector_model_id ?? "–"],
              ["Load shed", (meta as any)?.load_shed ? "yes" : "no"],
              ["Routing conf.", (meta as any)?.routing_confidence ?? "–"],
              ["Functions", (meta?.function_calls && meta.function_calls.length > 0) ? meta.function_calls.join(", ") : "–"],
            ].map(([label, val]) => (
              <div
                key={String(label)}
                className="w-full border border-vaultred/50 rounded-md px-3 py-2 flex items-center justify-between"
              >
                <span className="font-semibold">{label as string}</span>
                <span className="font-mono break-all">{String(val)}</span>
              </div>
            ))}
          </div>
        )}
          </div>
        {/* Fingerprint tab */}
        {activeTab === "fingerprints" && (
          <div className="space-y-3 text-sm text-copy break-all">
            {/* Request ID row */}
            <div className="flex items-center">
              <span className="font-semibold mr-1">Request ID:</span>
              <span className="ml-1 font-mono">{meta?.request_id ?? "–"}</span>
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
              <span className="ml-1 font-mono">{meta?.plan_fingerprint ?? "–"}</span>
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
              <span className="ml-1 font-mono">
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
              <span className="ml-1 font-mono">{meta?.snapshot_etag ?? "–"}</span>
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