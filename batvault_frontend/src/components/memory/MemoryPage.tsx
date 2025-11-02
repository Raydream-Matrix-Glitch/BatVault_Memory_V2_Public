import React, { useMemo, useState, useEffect, useCallback, useRef } from "react";
import Card from "./ui/Card";
import QueryPanel from "./QueryPanel";
import { normalizeErrorMessage } from "../../utils/errors";
import { useMemoryAPI } from "../../hooks/useMemoryAPI";
import type { GraphEdge } from "../../types/memory";
import AuditDrawer from "./AuditDrawer";
import Button from "./ui/Button";
import { logEvent } from "../../utils/logger";
import { currentRequestId } from "../../traceGlobals";
import { openPresignedBundle, openReceipt, normalizeRid } from "../../utils/bundle";
import EmptyResult from "./EmptyResult";
import AllowedIdsDrawer from "./AllowedIdsDrawer";
import ShortAnswer from "./ShortAnswer";
import { useEnrichedCatalog } from "../../hooks/useEnrichedCatalog";
import type { EnrichedNode } from "../../types/memory";


export default function MemoryPage() {
  const {
    tokens,
    isStreaming,
    error,
    finalData,
    queryDecision,
  } = useMemoryAPI();

  // --- UI-only validation message just below the QueryPanel input ---
  const [emptyHint, setEmptyHint] = useState<string | null>(null);
  const queryPanelHostRef = useRef<HTMLDivElement>(null);
  const [nextTitle] = useState<string | undefined>(undefined);
  const { loading: catalogLoading, error: catalogError, itemsById, load: loadCatalog } = useEnrichedCatalog();
  const [hasExpanded, setHasExpanded] = useState(false);
  const [miniById, setMiniById] = useState<Record<string, EnrichedNode>>({});
  // ---- Derived IDs/meta for effects & render (avoid TDZ/duplicate locals) ----
  const anchorId = useMemo(() => {
    const a = (finalData as any)?.anchor;
    return typeof a === "string" ? a : (a?.id || "");
  }, [finalData]);
  const snapshotEtag = (finalData as any)?.meta?.snapshot_etag || "";
  const policyFp     = (finalData as any)?.meta?.policy_fp || "";
  const allowedIdsFp = (finalData as any)?.meta?.allowed_ids_fp || "";
  const edges: GraphEdge[] = ((((finalData as any)?.graph)?.edges) || []) as GraphEdge[];
  const cited: string[] = Array.isArray((finalData as any)?.answer?.cited_ids)
    ? (finalData as any).answer.cited_ids
    : [];

  // Control the visibility of the audit drawer. It becomes available once finalData is present.
  const [auditOpen, setAuditOpen] = useState(false);

  // Allow other components (or the empty-state CTA) to switch users to the Natural path.
  const switchToNatural = useCallback(() => {
    try {
      logEvent("ui.memory.switch_to_natural_clicked", { rid: finalData?.meta?.request_id ?? null });
    } catch { /* ignore */ }
    try {
      // Broadcast a custom event QueryPanel can optionally listen to.
      window.dispatchEvent(new CustomEvent("bv:switchToNatural"));
      // Try to focus the shared input if present.
      (document.getElementById("memory-input") as HTMLInputElement | null)?.focus();
    } catch { /* ignore */ }
  }, [finalData]);

  const handleOpenAllowedIds = useCallback(async () => {
    try { logEvent("ui.allowed_ids.open_click", { rid: finalData?.meta?.request_id ?? null }); } catch {}

    // Proactively fetch the Enriched Catalog so the drawer always has data on open.
    try {
      const anchorId     = finalData?.anchor?.id || "";
      const snapshotEtag = finalData?.meta?.snapshot_etag || "";
      const allowedIds   = finalData?.meta?.allowed_ids || [];
      const policyFp     = finalData?.meta?.policy_fp || "";
      const allowedIdsFp = finalData?.meta?.allowed_ids_fp || "";
      const cacheKey     = `${snapshotEtag}|${allowedIdsFp}|${policyFp}`;

      if (anchorId && snapshotEtag && allowedIds.length > 0) {
        // Warm the cache; AllowedIdsDrawer will render from it immediately.
        await loadCatalog({ anchorId, snapshotEtag, allowedIds, cacheKey });
      } else {
        // Strategic logging: prefetch skipped due to missing prerequisites.
        try { logEvent("ui.allowed_ids.prefetch_skipped", {
          have_anchor: Boolean(anchorId),
          have_snapshot: Boolean(snapshotEtag),
          allowed_count: allowedIds.length
        }); } catch {}
      }
    } catch {
      // Non-fatal: the drawer will attempt again if needed.
    }

    // Signal the drawer to open and scroll it into view.
    try { window.dispatchEvent(new CustomEvent('open-allowed-ids')); } catch { /* no-op */ }
    setHasExpanded(true);
    try {
      const el = document.getElementById('allowed-ids-drawer');
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch { /* ignore */ }
  }, [finalData, loadCatalog])

  // Strategic structured logging for audit drawer interactions.
  const handleOpenAudit = () => {
    setAuditOpen(true);
    // Deterministic event key; include request id if present.
    logEvent("ui.audit_open", { rid: finalData?.meta?.request_id ?? null });
  };

  // Schema-first blocks
  const blocks = (finalData as any)?.answer?.blocks as any | undefined;

  const nextSlug: string | undefined = useMemo(() => {
    const edges: GraphEdge[] = ((finalData as any)?.graph?.edges) || [];
    const anchorId = (finalData as any)?.anchor?.id;
    const nxt = edges.find(
      (e) => e && e.type === "CAUSAL" && e.orientation === "succeeding" && e.from === anchorId
    );
    if (!nxt) return undefined;
    return String(nxt.to || "");
  }, [finalData]);

  // Immediately hydrate a minimal set to render the short answer + mini cause/effect.
  useEffect(() => {
    if (!anchorId || !snapshotEtag) return;
    // Build minimal ID set: anchor + cited_ids + endpoints of edges
    const endpointIds = new Set<string>();
    for (const e of edges || []) {
      if (typeof e?.from === "string") endpointIds.add(String(e.from));
      if (typeof e?.to === "string")   endpointIds.add(String(e.to));
    }
    const ids = Array.from(new Set([anchorId, ...cited, ...Array.from(endpointIds)]))
      .filter(id => typeof id === "string" && id.includes("#"))
      .sort();
    if (!ids.length) return;
    const cacheKey = `${snapshotEtag}|mini|${allowedIdsFp}|${policyFp}`;
    (async () => {
      try {
        const { itemsById: map } = await loadCatalog({ anchorId, snapshotEtag, allowedIds: ids, cacheKey });
        setMiniById(map || {});
      } catch {
        // Non-fatal; short-answer will fall back to blocks as-is.
      }
    })();
  // hydrate when the final response changes
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchorId, snapshotEtag, allowedIdsFp, policyFp, (finalData as any)?.graph?.edges, (finalData as any)?.answer?.cited_ids]);

  // Derive a better lead/owner from the enriched anchor if blocks are skeletal.
  const enrichedAnchor = miniById[anchorId] || itemsById?.[anchorId];
  const normalizedBlocks = useMemo(() => {
    const b: any = { ...(blocks || {}) };
    const looksLikeId = (s?: string) => !!s && typeof s === "string" && s.includes("#");
    const looksBad = !b?.lead || b.lead.trim().length < 4 || /(^eng\.?$)|(^id:?)/i.test(b.lead);
    if (looksBad && enrichedAnchor) {
      const ts  = typeof enrichedAnchor.timestamp === "string" ? enrichedAnchor.timestamp.slice(0,10) : "";
      const ttl = enrichedAnchor.title || "";
      const dm  = (enrichedAnchor as any)?.decision_maker;
      const who = (dm?.name ? (dm.role ? `${dm.name} (${dm.role})` : dm.name) : "");
      if (ttl) {
        b.lead = [ts, who].filter(Boolean).join(" ") + (ttl ? (who || ts ? ": " : "") + ttl : "");
      }
      if (!b.owner && who) {
        b.owner = { name: dm.name, role: dm.role };
      }
      b.decision_id = anchorId || b.decision_id;
      // Fill description from anchor if missing
      if (!b.description && typeof enrichedAnchor?.description === "string" && enrichedAnchor.description.trim()) {
        b.description = enrichedAnchor.description.trim();
      }
     }
    // Derive/repair Key events (preceding LED_TO → anchor), ranked by selector score then timestamp.
    if ((!b.key_events || b.key_events.length === 0) && miniById && anchorId && Array.isArray((finalData as any)?.graph?.edges)) {
      const edges: GraphEdge[] = ((finalData as any)?.graph?.edges) || [];
      const selectorScores: Record<string, number> = (finalData as any)?.meta?.selector_scores ?? {};
      const evIds = Array.from(new Set(
        edges
          .filter((e) => (e?.type === "LED_TO" || e?.type === "led_to") && e?.orientation === "preceding" && e?.to === anchorId)
          .map((e) => String(e.from))
      ));
      const events = evIds
        .map((id) => miniById[id])
        .filter((n): n is any => !!n && typeof n.title === "string" && n.title.trim());
      events.sort((a: any, b: any) => {
        const sa = selectorScores[a.id] ?? -Infinity;
        const sb = selectorScores[b.id] ?? -Infinity;
        if (sa !== sb) return sb - sa;
        const ta = Date.parse(a.timestamp || "") || 0;
        const tb = Date.parse(b.timestamp || "") || 0;
        return tb - ta || String(a.id).localeCompare(String(b.id));
      });
      b.key_events = events.slice(0, 3).map((n: any) => n.title.replace(/[.;:,]\s*$/, ""));
    } else if (Array.isArray(b.key_events) && b.key_events.every(looksLikeId)) {
      // Hydrate gateway-provided ID fallbacks to titles
      b.key_events = (b.key_events as string[])
        .map((id) => miniById[id]?.title || id)
        .filter(Boolean)
        .map((t: string) => t.replace(/[.;:,]\s*$/, ""));
    }
    // Derive/repair "Next:" from succeeding CAUSAL → title (also hydrate ID fallback)
    if ((!b.next || looksLikeId(b.next)) && nextSlug && miniById[nextSlug]?.title) {
      b.next = String(miniById[nextSlug].title).replace(/[.;:,]\s*$/, "");
    }

    return b;
  }, [blocks, enrichedAnchor, anchorId, finalData, miniById, nextSlug]);

  // Disable native browser validation bubbles inside QueryPanel so we can show our own hint.
  useEffect(() => {
    const host = queryPanelHostRef.current;
    if (!host) return;
    const form = host.querySelector("form");
    if (form) (form as HTMLFormElement).setAttribute("novalidate", "true");
    host.querySelectorAll("input[required]").forEach((el) => {
      (el as HTMLInputElement).removeAttribute("required");
    });
  }, []);

  // Log presence of a "Next:" line for audit/UX metrics
  useEffect(() => {
    if (nextTitle) {
      try {
        logEvent("ui.memory.next_line_present", {
          next: nextTitle,
          rid: finalData?.meta?.request_id ?? null
        });
      } catch { /* ignore */ }
    }
  }, [nextTitle, finalData?.meta?.request_id]);

  // Emit a "short answer rendered" event whenever a final short answer is
  // available. Use the anchor id as payload if present.
  useEffect(() => {
    if (finalData && finalData.answer && finalData.answer.short_answer) {
      try {
        logEvent("ui_short_answer_rendered", {
          id: finalData?.anchor?.id ?? null,
        });
      } catch {
        /* ignore logging errors */
      }
    }
  }, [finalData]);

  // Extract supplementary answer fields for UI enhancements
  const _citationIds: string[] = finalData?.answer?.cited_ids ?? finalData?.answer?.supporting_ids ?? [];
  useEffect(() => {
    try {
      logEvent("ui.memory.layout_painted", { rid: finalData?.meta?.request_id ?? null });
    } catch {
      /* ignore logging errors */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Evidence bundle download (V3): view / full
  const handleDownloadEvidence = useCallback(async () => {
    try { logEvent("ui.evidence_download_click", { rid: finalData?.meta?.request_id ?? null }); } catch { /* ignore */ }
    const bundleUrl = (finalData as any)?.bundle_url as string | undefined;
    const ridFromMeta = ((finalData as any)?.meta?.request_id as string | undefined) || currentRequestId();
    let rid = ridFromMeta;
    if (!rid && bundleUrl) {
      const m = /\/v3\/bundles\/([^\/\s]+)/i.exec(bundleUrl);
      if (m && m[1]) rid = m[1];
    }
    // Clean path: authoritative bundle.ts handles RID shape + presign
    if (rid) {
      const nrid = normalizeRid(rid);
      await openPresignedBundle(nrid, "bundle_view");
    } else {
      // No RID found anywhere → log for forensics, do nothing
      try { logEvent("ui.evidence_download_missing_rid", { bundle_url: bundleUrl || null }); } catch {}
    }
  }, [finalData]);

  const handleDownloadFull = useCallback(async () => {
    try { logEvent("ui.evidence_download_full_click", { rid: (finalData as any)?.meta?.request_id ?? null }); } catch {}
    const rid = ((finalData as any)?.meta?.request_id as string | undefined) || currentRequestId();
    if (rid) {
      await openPresignedBundle(normalizeRid(rid), "bundle_full");
    }
  }, [finalData]);

  // Central handler for clicking a receipt chip inside ShortAnswer
  const handleCitationClick = useCallback((cid: string) => {
    try {
      logEvent("ui.citation_click", { id: cid, rid: finalData?.meta?.request_id ?? null });
    } catch { /* ignore logging errors */ }
    setSelectedEvidenceId(cid);
    const el = document.getElementById(`evidence-${cid}`);
    if (el) {
      try { el.scrollIntoView({ behavior: "smooth", block: "start" }); } catch {}
    }
  }, [finalData]);

  // Log when the empty-state is shown (no edges returned).
  useEffect(() => {
    const edgeCount = Array.isArray((finalData as any)?.graph?.edges)
      ? ((finalData as any).graph.edges as any[]).length
      : 0;
    if (!isStreaming && edgeCount === 0) {
      try {
        logEvent("ui.memory.empty_state_shown", {
          rid: finalData?.meta?.request_id ?? null,
          reason: "EMPTY_EDGES",
        });
      } catch { /* ignore */ }
    }
  }, [isStreaming, finalData]);

  // When final data arrives, expose its request id on the window for debug logs.
  useEffect(() => {
    if (finalData && finalData.meta) {
      (window as any).__lastRid = finalData?.meta?.request_id ?? null;
    }
  }, [finalData]);

  return (
    <div
      className="w-full max-w-[1040px] mx-auto px-6 space-y-8"
      style={{ marginRight: auditOpen && finalData ? `calc(28rem + 24px)` : undefined }}
    >
      <Card className="relative mx-auto w-full max-w-[1024px] pr-6">
        {error && (
          <div className="mt-3 mb-3 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm">
            {normalizeErrorMessage(error)}
          </div>
        )}
       {/* streaming progress bar */}
        {isStreaming && (
          <div className="absolute inset-x-0 top-0 h-[2px] bg-vaultred/70 animate-pulse" />
        )}
        {/* heading — BatVault Memory (Origins colors/glow, no dots, side-by-side) */}
        {/* Explicit first-paint warning when there are no edges */}
        {!isStreaming && Array.isArray((finalData as any)?.graph?.edges)
          && ((finalData as any)?.graph?.edges?.length === 0) && (
          <div className="mt-3 mb-1 rounded-lg border border-white/10 bg-white/5 p-3 text-xs">
            No related edges in policy scope for first paint. Short answer and citations are still shown.
          </div>
        )}
        <h1 className="flex items-baseline gap-2 mb-6">
          {/* BatVault slightly larger */}
          <span className="font-extrabold tracking-tight text-vaultred md:text-3xl text-3xl opacity-80">
            BatVault
          </span>
          <span className="font-extrabold tracking-tight text-neonCyan md:text-3xl text-3xl opacity-80">
            Memory
          </span>
        </h1>
        <p className="text-copy/90 mb-7">
          Enter a decision reference.
        </p>
        {/* Query panel */}
        <div ref={queryPanelHostRef}>
          <QueryPanel
            onQueryDecision={(decisionRef: string) => {
              const slug = decisionRef?.trim();
              if (!slug) {
                setEmptyHint("I can’t trace the void — drop a decision id.");
                return;
              }
              setEmptyHint(null);
              logEvent("ui.memory.query_decision", { decision_ref: slug });
              return queryDecision(slug);
            }}
            isStreaming={isStreaming}
          />
          {emptyHint && (
            <div role="alert" className="validation-pop mt-2">
              {emptyHint}
            </div>
          )}
        </div>
        {/* Warning when final payload shape is unexpected (no blocks) */}
        {!isStreaming && !blocks && (finalData as any) && (
          <div className="mt-6 pt-4 border-t border-amber-500">
            <p className="text-amber-300 font-mono text-sm">
              No renderable payload from /query. Expected <code>answer.blocks</code> and <code>graph.edges</code>.
              If the API wraps fields under <code>response</code>, the FE must unwrap it in <code>useMemoryAPI</code>.
            </p>
          </div>
        )}
        {/* If the server returned no edges, show a gentle notice */}
        {!isStreaming && Array.isArray(((finalData as any)?.graph?.edges)) && ((finalData as any).graph.edges.length === 0) && (
          <div className="mt-6 pt-4 border-t border-white/10">
            <p className="text-copy/80 text-sm">
              No edges found for this anchor in the snapshot. If you expected a graph, check <code>meta.snapshot_etag</code>
              and whether this decision has any <code>LED_TO</code>/<code>CAUSAL</code> links.
            </p>
          </div>
        )}
        {(isStreaming || (finalData && blocks)) && (
          <>
            <ShortAnswer
              isStreaming={isStreaming}
              tokens={tokens as any}
              blocks={normalizedBlocks}
              receipts={cited}
              onCitationClick={handleCitationClick}
              onOpenAllowedIds={finalData ? handleOpenAllowedIds : undefined}
              onOpenAudit={finalData ? handleOpenAudit : undefined}
            />
            {/* Receipt + actions */}
          </>

        )}
        {/* All Allowed IDs (expandable enriched catalog) */}
        {!isStreaming && finalData && (
          <AllowedIdsDrawer
            anchorId={(typeof (finalData as any)?.anchor === "string"
              ? String((finalData as any).anchor)
              : String((finalData as any)?.anchor?.id || ""))}
            edges={(((finalData as any)?.graph?.edges) || []) as GraphEdge[]}
            anchor={finalData?.anchor as any}
            allowedIds={finalData?.meta?.allowed_ids || []}
            policyFp={finalData?.meta?.policy_fp || ""}
            allowedIdsFp={finalData?.meta?.allowed_ids_fp || ""}
            snapshotEtag={finalData?.meta?.snapshot_etag || ""}
            loadCatalog={loadCatalog}
            loading={catalogLoading}
            error={catalogError}
          />
        )}

        {/* Error state */}
        {error && !isStreaming && (
          <div className="mt-6 pt-4 border-t border-red-500">
            <p className="text-red-400 font-mono text-sm">Error: {normalizeErrorMessage(error)}</p>
          </div>
        )}
      </Card>
      {/* Off-canvas Audit Drawer */}
      {finalData && (
        <AuditDrawer
          open={auditOpen}
          onClose={() => {
            logEvent("ui.audit_close", { rid: finalData?.meta?.request_id ?? null });
            setAuditOpen(false);
          }}
          initialTab="fingerprints"
          meta={finalData.meta} requestId={(finalData as any)?.meta?.request_id || currentRequestId()}
          evidence={finalData.evidence}
          answer={finalData.answer}
          bundle_url={finalData.bundle_url}
        />
      )}
    </div>
  );
}
