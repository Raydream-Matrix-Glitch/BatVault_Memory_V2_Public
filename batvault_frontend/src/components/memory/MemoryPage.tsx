import React, { useMemo, useState, useEffect, useCallback } from "react";
import Card from "./ui/Card";
import QueryPanel from "./QueryPanel";
import TokenStreamLine from "./ui/TokenStreamLine";
import EvidenceList from "./EvidenceList";
import { useMemoryAPI } from "../../hooks/useMemoryAPI";
import type { EvidenceItem, EvidenceBundle } from "../../types/memory";
import AuditDrawer from "./AuditDrawer";
import Button from "./ui/Button";
import TagCloud from "./TagCloud";
import SchemaExplorer from "./SchemaExplorer";
import GraphView from "./GraphView";
import { motion } from "framer-motion";

/**
 * Root container for the Memory page.
 *
 * This component will evolve in later batches to include the full
 * query interface, streaming answer renderer, evidence cards,
 * audit drawer and data visualisations. For now, it provides a
 * skeleton with cyberpunk styling to verify that routing and
 * theming are wired correctly.
 */
export default function MemoryPage() {
  const {
    tokens,
    isStreaming,
    error,
    finalData,
    ask,
    query,
    cancel,
  } = useMemoryAPI();

  // Track which evidence item is currently selected (for graph/audit integration).
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | undefined>(undefined);

  // Control the visibility of the audit drawer. It becomes available once finalData is present.
  const [auditOpen, setAuditOpen] = useState(false);

  // Track a tag filter selected from the tag cloud. Undefined means no filter.
  const [tagFilter, setTagFilter] = useState<string | undefined>(undefined);

  // Strategic structured logging for audit drawer interactions.
  const handleOpenAudit = () => {
    setAuditOpen(true);
    // Deterministic event key; include request id if present.
    console.debug("[memory.audit.open]", { evt: "audit_open", rid: finalData?.meta?.request_id ?? null });
  };
  const handleCloseAudit = () => {
    setAuditOpen(false);
    console.debug("[memory.audit.close]", { evt: "audit_close", rid: finalData?.meta?.request_id ?? null });
  };

  const shortAnswer = finalData?.answer?.short_answer;

  // Extract supplementary answer fields for UI enhancements
  const citationIds: string[] = finalData?.answer?.supporting_ids ?? [];
  const anchorMaker: string | undefined = finalData?.evidence?.anchor?.decision_maker;
  const anchorTs: string | undefined = finalData?.evidence?.anchor?.timestamp;
  const fallbackUsed: boolean | undefined = finalData?.meta?.fallback_used;
  const badgeLabel: string = fallbackUsed ? "Deterministic" : "LLM-polished";
  const nextTarget = finalData?.evidence?.transitions?.succeeding?.[0];
  const nextLabel: string | undefined =
    (nextTarget && (nextTarget as any).id) ||
    (nextTarget && (nextTarget as any).summary) ||
    (nextTarget && (nextTarget as any).snippet) ||
    undefined;

  // Log when anchor maker/date chips are present
  useEffect(() => {
    if ((anchorMaker || anchorTs) && finalData?.meta?.request_id) {
      try {
        console.debug("[ui.memory.anchor_seen]", {
          maker: anchorMaker ?? null,
          ts: anchorTs ?? null,
          rid: finalData.meta.request_id,
        });
      } catch {
        /* ignore logging errors */
      }
    }
  }, [anchorMaker, anchorTs, finalData]);

  // Handler for evidence bundle download. Prefers a presigned URL when available.
  const handleDownloadEvidence = useCallback(async () => {
    const rid = finalData?.meta?.request_id;
    const bundleUrl = (finalData as any)?.bundle_url as string | undefined;
    // Basic sanity
    if (!rid) return;

    // Attempt canonical flow: POST /v2/bundles/{rid}/download → {url, expires_in}
    try {
      console.debug("[ui.memory.bundle_download]", {
        rid,
        flow: "post_then_get",
      });
    } catch { /* ignore logging errors */ }

    try {
      const resp = await fetch(`/v2/bundles/${rid}/download`, { method: "POST" });
      if (resp.ok) {
        const data = await resp.json().catch(() => null) as any;
        const url = data && typeof data.url === "string" ? data.url : undefined;
        try {
          console.debug("[ui.memory.bundle_download.presigned_received]", {
            rid,
            has_url: !!url,
            expires_in: (data && data.expires_in) || null,
          });
        } catch { /* ignore logging errors */ }
        if (url) {
          window.open(url, "_blank");
          return;
        }
      }
    } catch {
      // swallow and fall through to legacy fallbacks
    }

    // Fallback 1: legacy bundle_url on the response (if present)
    if (bundleUrl) {
      try {
        console.debug("[ui.memory.bundle_download.fallback_bundle_url]", { rid });
      } catch { /* ignore logging errors */ }
      window.open(bundleUrl, "_blank");
      return;
    }

    // Fallback 2: direct GET to the gateway/edge bundle endpoint
    try {
      console.debug("[ui.memory.bundle_download.fallback_direct_get]", { rid, path: `/v2/bundles/${rid}` });
    } catch { /* ignore logging errors */ }
    try {
      const resp = await fetch(`/v2/bundles/${rid}`);
      if (!resp.ok) throw new Error(`Failed to download bundle: ${resp.status}`);
      const blob = await resp.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${rid}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);
    } catch {
      /* swallow errors silently */
    }
  }, [finalData]);

  // Flatten the evidence bundle into a single array of items. Anchor and events
  // come first; transitions (preceding/succeeding) follow. This memoised
  // computation avoids recalculation on every render.
  const evidenceItems: EvidenceItem[] = useMemo(() => {
    const bundle: EvidenceBundle | undefined = finalData?.evidence;
    if (!bundle) return [];
    const items: EvidenceItem[] = [];
    if (bundle.anchor) items.push({ ...bundle.anchor });
    if (bundle.events) items.push(...bundle.events.map((e) => ({ ...e })));
    if (bundle.transitions) {
      if (bundle.transitions.preceding) items.push(...bundle.transitions.preceding.map((t) => ({ ...t })));
      if (bundle.transitions.succeeding) items.push(...bundle.transitions.succeeding.map((t) => ({ ...t })));
    }
    return items;
  }, [finalData]);

  // Compute tag frequencies for the tag cloud
  const tagCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    evidenceItems.forEach((item) => {
      item.tags?.forEach((tag) => {
        counts[tag] = (counts[tag] || 0) + 1;
      });
    });
    return counts;
  }, [evidenceItems]);

  // Apply tag filter to evidence items
  const filteredEvidenceItems = useMemo(() => {
    if (!tagFilter) return evidenceItems;
    return evidenceItems.filter((item) => item.tags?.includes(tagFilter));
  }, [evidenceItems, tagFilter]);

  // When final data arrives, expose its request id on the window for debug logs.
  useEffect(() => {
    if (finalData && finalData.meta) {
      (window as any).__lastRid = finalData?.meta?.request_id ?? null;
    }
  }, [finalData]);

  return (
    <div className="w-full max-w-4xl mx-auto p-4 space-y-6">
      <Card className="relative">
       {/* streaming progress bar */}
        {isStreaming && (
          <div className="absolute inset-x-0 top-0 h-[2px] bg-vaultred/70 animate-pulse" />
        )}
        {/* encrypted status pill */}
        <motion.div
          className="absolute top-4 right-4 flex items-center space-x-2"
          aria-label="status"
        >
          <motion.div
            className="w-3 h-3 rounded-full bg-green-500"
           animate={{ opacity: [0.3, 1, 0.3] }}
            transition={{ duration: 2, ease: "easeInOut", repeat: Infinity }}
          />
          <span className="text-xs font-mono text-vaultred">ENCRYPTED</span>
        </motion.div>
        {/* heading */}
        <h1 className="flex items-center text-2xl font-bold text-vaultred mb-2">
          <span className="heading-accent" />
          BatVault Memory Interface
        </h1>
        <p className="text-copy/90 mb-3">
          Enter a decision reference or ask a natural question. Results will stream in
          real time.
        </p>
        {/* Query panel */}
        <QueryPanel onAsk={ask} onQuery={query} isStreaming={isStreaming} />
        {/* Streaming answer */}
        {tokens.length > 0 && (
          <div className="mt-4">
            <TokenStreamLine tokens={tokens} />
          </div>
        )}
        {/* Final answer summary */}
        {shortAnswer && !isStreaming && (
          <div className="mt-4 p-3 border-t border-gray-700">
            <div className="flex justify-between items-start">
              <div className="flex-1 mr-4">
                <h2 className="text-lg font-semibold text-vaultred mb-1">Short answer</h2>
                <p className="text-copy mb-2">{shortAnswer}</p>
                {/* Citation pills: render up to the first three supporting IDs */}
                {citationIds && citationIds.length > 0 && (
                  <div className="flex flex-wrap gap-2 mb-2">
                    {citationIds.slice(0, 3).map((cid) => (
                      <button
                        key={cid}
                        type="button"
                        onClick={() => {
                          try {
                            console.debug("[ui.memory.citation_click]", {
                              id: cid,
                              rid: finalData?.meta?.request_id ?? null,
                            });
                          } catch {
                            /* ignore logging errors */
                          }
                        }}
                        className="text-xs font-mono px-2 py-0.5 rounded-full border border-vaultred/50 text-vaultred hover:bg-vaultred/30 transition-colors"
                      >
                        {cid}
                      </button>
                    ))}
                  </div>
                )}
                {/* Maker/Date chips */}
                {(anchorMaker || anchorTs) && (
                  <div className="flex flex-wrap gap-2 mb-2">
                    {anchorMaker && (
                      <span className="text-xs font-mono px-2 py-0.5 rounded-full border border-vaultred/50 text-neonCyan">
                        {anchorMaker}
                      </span>
                    )}
                    {anchorTs && (
                      <span className="text-xs font-mono px-2 py-0.5 rounded-full border border-vaultred/50 text-neonCyan">
                        {anchorTs}
                      </span>
                    )}
                  </div>
                )}
              </div>
              {/* Fallback badge */}
              <div className="self-start">
                <span className="text-xs font-mono px-2 py-1 rounded-md border border-vaultred/50 text-vaultred whitespace-nowrap">
                  {badgeLabel}
                </span>
              </div>
            </div>
            {/* Next transition line */}
            {nextLabel && (
              <div className="mt-1 text-xs text-copy">
                <span className="font-semibold text-vaultred mr-1">Next:</span>
                <span>{nextLabel}</span>
              </div>
            )}
            {/* Download evidence button */}
            <div className="mt-3 flex justify-end">
              <Button
                onClick={handleDownloadEvidence}
                variant="secondary"
                className="text-xs"
              >
                Download evidence
              </Button>
            </div>
          </div>
        )}
        {/* Evidence list */}
        {!isStreaming && finalData?.evidence && (
          <div className="mt-6">
            {/* Sticky subheader row containing the Evidence heading and tag cloud */}
            <div className="sticky top-0 bg-surface/80 backdrop-blur py-2 z-10">
              <h2 className="text-lg font-semibold text-vaultred mb-2">Evidence</h2>
              {/* Tag cloud for filtering evidence. Always render the heading; if no tags are present,
               * inform the user. This ensures the “Filter by tag” header is visible even when the API
               * returns no tags. */}
              <div className="mb-2">
                <h3 className="text-sm font-semibold text-vaultred mb-1">Filter by tag</h3>
                {Object.keys(tagCounts).length > 0 ? (
                  <TagCloud
                    tags={tagCounts}
                    selected={tagFilter}
                    onSelect={(tag) => setTagFilter(tag)}
                  />
                ) : (
                  <p className="text-copy text-xs italic">
                    No tags found in this evidence bundle.
                  </p>
                )}
              </div>
            </div>
            {filteredEvidenceItems.length > 0 ? (
              <>
                {/* Wrap multiple siblings in a fragment to fix syntax error */}
                <EvidenceList
                  items={filteredEvidenceItems}
                  selectedId={selectedEvidenceId}
                  onSelect={(id) => setSelectedEvidenceId(id)}
                  className="mt-2"
                />
                {/* Graph view for visualising relationships between evidence items */}
                <div className="mt-6">
                  <h3 className="text-sm font-semibold text-vaultred mb-1">Relation graph</h3>
                  <GraphView
                    items={filteredEvidenceItems}
                    selectedId={selectedEvidenceId}
                    onSelect={(id) => setSelectedEvidenceId(id)}
                  />
                </div>
              </>
            ) : (
              <p className="text-copy text-sm">No evidence returned.</p>
            )}
          </div>
        )}
        {/* Audit drawer trigger */}
        {(tokens.length > 0 || isStreaming || finalData) && (
          <div className="mt-6 flex justify-end">
            <Button
              onClick={handleOpenAudit}
              disabled={!finalData}
              className={!finalData ? "opacity-50 cursor-not-allowed" : undefined}
            >
              Audit
            </Button>
          </div>
        )}
        {/* Error state */}
        {error && !isStreaming && (
          <div className="mt-4 p-3 border-t border-red-500">
            <p className="text-red-400 font-mono text-sm">Error: {error.message}</p>
          </div>
        )}
      </Card>
      {/* Schema Explorer shown below the main card */}
      <SchemaExplorer />
      {/* Off-canvas Audit Drawer */}
      {finalData && (
        <AuditDrawer
          open={auditOpen}
          onClose={() => {
            console.debug("[memory.audit.close]", { evt: "audit_close", rid: finalData?.meta?.request_id ?? null });
            setAuditOpen(false);
          }}
          meta={finalData.meta}
          evidence={finalData.evidence}
          answer={finalData.answer}
        />
      )}
    </div>
  );
}
