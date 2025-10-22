import React, { useMemo, useState, useEffect, useCallback, useRef } from "react";
import Card from "./ui/Card";
import QueryPanel from "./QueryPanel";
import EvidenceList from "./EvidenceList";
import { useMemoryAPI } from "../../hooks/useMemoryAPI";
import type { EvidenceItem, EvidenceBundle, GraphEdge } from "../../types/memory";
import AuditDrawer from "./AuditDrawer";
import Button from "./ui/Button";
import { logEvent } from "../../utils/logger";
import { openEvidenceBundle } from "../../utils/bundle";
import TagFilter from "./ui/TagFilter";
import EmptyResult from "./EmptyResult";
import AllowedIdsDrawer from "./AllowedIdsDrawer";
import ShortAnswer from "./ShortAnswer";
import { useEnrichedCatalog } from "../../hooks/useEnrichedCatalog";


export default function MemoryPage() {
  const {
    tokens,
    isStreaming,
    error,
    finalData,
    queryDecision,
    topic,
  } = useMemoryAPI();

  // --- UI-only validation message just below the QueryPanel input ---
  const [emptyHint, setEmptyHint] = useState<string | null>(null);
  const queryPanelHostRef = useRef<HTMLDivElement>(null);

  // Next decision (derived from edges); no alias resolver in V3 cutover
  const [nextTitle] = useState<string | undefined>(undefined);

  // Enriched Catalog hook (for All Allowed IDs)
  const { loading: catalogLoading, error: catalogError, itemsById, load: loadCatalog } = useEnrichedCatalog();

  // Auto-prefetch Enriched Catalog once final bundle arrives (edges-only baseline).
  useEffect(() => {
    const anchorId     = finalData?.anchor?.id || "";
    const snapshotEtag = finalData?.meta?.snapshot_etag || "";
    const allowedIds   = (finalData as any)?.meta?.allowed_ids || (finalData as any)?.evidence?.allowed_ids || [];
    const policyFp     = finalData?.meta?.policy_fp || "";
    const allowedIdsFp = finalData?.meta?.allowed_ids_fp || "";
    const cacheKey     = `${snapshotEtag}|${allowedIdsFp}|${policyFp}`;
    if (anchorId && snapshotEtag && allowedIds.length > 0) {
      try {
        logEvent("ui.allowed_ids.prefetch_autorun", {
          have_anchor: true,
          have_snapshot: true,
          allowed_count: allowedIds.length,
          rid: finalData?.meta?.request_id ?? null
        });
      } catch { /* no-op */ }
      // Deterministic, cache-keyed prefetch (idempotent inside hook).
      loadCatalog({ anchorId, snapshotEtag, allowedIds, cacheKey });
    }
  }, [finalData?.meta?.snapshot_etag, finalData?.meta?.allowed_ids_fp, finalData?.meta?.policy_fp, loadCatalog, finalData?.anchor?.id]);


  // Track which evidence item is currently selected (for graph/audit integration).
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | undefined>(undefined);

  // Control the visibility of the audit drawer. It becomes available once finalData is present.
  const [auditOpen, setAuditOpen] = useState(false);

  // Tag filtering: multi-select + AND/OR mode.
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const toggleTag = useCallback((tag: string) => {
    setSelectedTags((prev) => (prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]));
  }, []);

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
      const allowedIds   = finalData?.evidence?.allowed_ids || finalData?.meta?.allowed_ids || [];
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

  const rawShortAnswer = finalData?.answer?.short_answer;
  // Render-time split: if the short answer (or streaming tokens) contains
  // an inline "Next:" sentence, render it on a new line with a spacer.
  const streamingText = isStreaming
    ? (Array.isArray(tokens) ? tokens.join("") : String(tokens ?? ""))
    : undefined;
  const { mainAnswer, nextFromShort } = useMemo(() => {
    const src = (streamingText ?? rawShortAnswer) || "";
    const match = src.match(/\bNext:\s*(.*)$/i);
    if (!match) {
      return { mainAnswer: src, nextFromShort: undefined };
    }
    const before = src.slice(0, match.index).trim();
    const nextTxt = match[1].trim();
    try { logEvent("ui.short_answer.next_split", { rid: finalData?.meta?.request_id ?? null }); } catch {}
    return { mainAnswer: before, nextFromShort: nextTxt };
  }, [streamingText, rawShortAnswer, finalData]);
  // Parse deterministic 'Key Events:' clause into a bullet-friendly array when we have the final short answer.
  const { leadBeforeEvents, eventsFromShort } = useMemo(() => {
    // Only parse once we have the final short answer to avoid flicker during streaming.
    if (!rawShortAnswer) return { leadBeforeEvents: undefined, eventsFromShort: undefined as string[] | undefined };
    try {
      const s = String(rawShortAnswer);
      // Capture the minimal-span events clause; templater always ends it with a period.
      const m = s.match(/\bKey\s*Events:\s*(.*?)(?:\.\s*(?:Next:|$))/i);
      if (!m) return { leadBeforeEvents: undefined, eventsFromShort: undefined };
      const lead = s.slice(0, m.index).trim();
      // Split on semicolons (templater joins with '; ') and trim items.
      const items = m[1].split(/\s*;\s*/).map(x => x.trim()).filter(Boolean);
      try { logEvent("ui.short_answer.key_events_parsed", { count: items.length, rid: finalData?.meta?.request_id ?? null }); } catch {}
      return { leadBeforeEvents: lead, eventsFromShort: items };
    } catch {
      return { leadBeforeEvents: undefined, eventsFromShort: undefined };
    }
  }, [rawShortAnswer, finalData]);
  const shortAnswer = useMemo(() => {
    if (!rawShortAnswer) return undefined;
    try {
      const parts = String(rawShortAnswer).split(/(?<=[.!?])\s+/).slice(0, 2);
      return parts.join(" ");
    } catch { return rawShortAnswer; }
  }, [rawShortAnswer]);

  const { anchorHeading, anchorDescription } = useMemo(() => {
    const src = (leadBeforeEvents ?? mainAnswer ?? "").trim();
    if (!src) return { anchorHeading: undefined as string | undefined, anchorDescription: undefined as string | undefined };
    try {
      const m = src.match(/^\s*(.+?)\s+on\s+(\d{4}-\d{2}-\d{2})\s*(?::|[—-])?\s*(.+?)\s*(?:[—-])\s*(.*)$/);
      if (!m) return { anchorHeading: undefined, anchorDescription: undefined };
      const who = m[1].trim();
      const date = m[2].trim();
      const title = m[3].trim();
      let desc = m[4].trim();
      // Avoid trailing period duplication when followed by "Key Events:" which we split out.
      desc = desc.replace(/\s*\.$/, "");
      return { anchorHeading: `${who} on ${date}: ${title}`, anchorDescription: desc };
    } catch {
      return { anchorHeading: undefined, anchorDescription: undefined };
    }
  }, [leadBeforeEvents, mainAnswer]);

  // v3: Compute the next decision from oriented edges (succeeding CAUSAL from anchor).
  const nextSlug: string | undefined = useMemo(() => {
    const edges: GraphEdge[] =
      ((finalData as any)?.graph?.edges) ||
      ((finalData as any)?.evidence?.graph?.edges) ||
      [];
    const anchorId = (finalData as any)?.anchor?.id || (finalData as any)?.evidence?.anchor?.id;
    const nxt = edges.find((e) => e && e.type === "CAUSAL" && e.orientation === "succeeding" && e.from === anchorId);
    if (!nxt) return undefined;
    return String(nxt.to || "");
  }, [finalData]);

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
  const citationIds: string[] = finalData?.answer?.cited_ids ?? finalData?.answer?.supporting_ids ?? [];
  useEffect(() => {
    try {
      logEvent("ui.memory.layout_painted", { rid: finalData?.meta?.request_id ?? null });
    } catch {
      /* ignore logging errors */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Evidence bundle download (V3):
  const handleDownloadEvidence = useCallback(async () => {
    try { logEvent("ui.evidence_download_click", { rid: finalData?.meta?.request_id ?? null }); } catch { /* ignore */ }
    const bundleUrl = (finalData as any)?.bundle_url as string | undefined;
    const ridFromMeta = (finalData as any)?.meta?.request_id as string | undefined;
    let rid = ridFromMeta;
    if (!rid && bundleUrl) {
      const m = /\/v3\/bundles\/([^\/\s]+)/i.exec(bundleUrl);
      if (m && m[1]) rid = m[1];
    }
    // Static import — avoids runtime chunk/load flakiness
    await openEvidenceBundle(rid, bundleUrl);
  }, [finalData]);

  // Build separate arrays for anchor, events, and transitions
  const anchorId = finalData?.evidence?.anchor?.id;
  const eventsOnly: EvidenceItem[] = useMemo(() => {
    const bundle: EvidenceBundle | undefined = finalData?.evidence;
    if (!bundle) return [];
    const evts = Array.isArray(bundle.events) ? bundle.events.map((e) => ({ ...e, type: 'EVENT' as const })) : [];
    // Ensure ids are present
    return evts.filter((e) => typeof (e as any).id === "string" && e.id);
  }, [finalData]);

  const anchorItem: EvidenceItem | undefined = useMemo(() => {
    const a = finalData?.evidence?.anchor as any;
    return a && a.id ? { ...a, type: 'DECISION' as const } : undefined;
  }, [finalData]);

  // Rank events by selector score (desc) then timestamp (desc), then id
  const selectorScores: Record<string, number> = finalData?.meta?.selector_scores ?? {};
  const rankedEvents: EvidenceItem[] = useMemo(() => {
    const copy = [...eventsOnly];
    copy.sort((a, b) => {
      const sa = selectorScores[a.id] ?? -Infinity;
      const sb = selectorScores[b.id] ?? -Infinity;
      if (sa !== sb) return sb - sa;
      const ta = Date.parse(a.timestamp || "") || 0;
      const tb = Date.parse(b.timestamp || "") || 0;
      if (ta !== tb) return tb - ta;
      return (a.id || "").localeCompare(b.id || "");
    });
    // Take top 10 for display
    return copy.slice(0, 10);

  }, [eventsOnly, selectorScores]);

  // Aggregate all evidence for the list: anchor → preceding transitions → ranked events → succeeding transitions
  const allEvidenceItems: EvidenceItem[] = useMemo(() => {
    const items: EvidenceItem[] = [];
    if (anchorItem) items.push(anchorItem);
    if (rankedEvents?.length) items.push(...rankedEvents);
    return items;
    }, [anchorItem, rankedEvents]);

  // Events only for graph (exclude decision/transitions)
  const filteredEventsForGraph: EvidenceItem[] = useMemo(() => {
    const predicate = (it: EvidenceItem) => {
      const t = it.tags || [];
      return selectedTags.some((tg) => t.includes(tg));
    };
    if (selectedTags.length === 0) return rankedEvents;
    return rankedEvents.filter(predicate);
  }, [rankedEvents, selectedTags]);

  // Build receipts strip separately (prevents nested useMemo).
  // Order: anchor → all cited_ids (preferred) / supporting_ids (fallback) (no cap).
  const receipts: string[] = useMemo(() => {
    const ids = new Set<string>();
    if (anchorId) ids.add(anchorId); // anchor from evidence
    ((finalData?.answer?.cited_ids ?? finalData?.answer?.supporting_ids) ?? []).forEach((id: string) => ids.add(id));
    return Array.from(ids);
  }, [anchorId, finalData]);

  useEffect(() => {
    try { logEvent("ui.receipts_count", { count: receipts.length, rid: finalData?.meta?.request_id ?? null }); } catch {}
  }, [receipts, finalData]);

  // Compute tag frequencies for the tag cloud
  const tagCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    allEvidenceItems.forEach((item) => {
      (item.tags ?? []).forEach((tag) => { counts[tag] = (counts[tag] || 0) + 1; });
    });
    return counts;
  }, [allEvidenceItems]);

  // Apply multi-tag filter (AND/OR) to all evidence (anchor always shown at top)
  const filteredEvidenceItems = useMemo(() => {
    const items = allEvidenceItems.slice();
    if (items.length === 0) return items;
    const anchor = items[0]?.type === 'DECISION' ? items[0] : undefined;
    const rest = anchor ? items.slice(1) : items;
    if (selectedTags.length === 0) return items;
    const predicate = (it: EvidenceItem) => {
      const t = it.tags || [];
      return selectedTags.some((tg) => t.includes(tg));
    };
    const filtered = rest.filter(predicate);
    return anchor ? [anchor, ...filtered] : filtered;
  }, [allEvidenceItems, selectedTags]);

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

  // Log when the empty-state is shown (no evidence returned).
  useEffect(() => {
    if (!isStreaming && finalData?.evidence && filteredEvidenceItems.length === 0) {
      try {
        logEvent("ui.memory.empty_state_shown", {
          rid: finalData?.meta?.request_id ?? null,
          reason: "EMPTY_EVIDENCE",
        });
      } catch { /* ignore */ }
    }
  }, [isStreaming, finalData, filteredEvidenceItems]);

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
       {/* streaming progress bar */}
        {isStreaming && (
          <div className="absolute inset-x-0 top-0 h-[2px] bg-vaultred/70 animate-pulse" />
        )}
        {/* heading — BatVault Memory (Origins colors/glow, no dots, side-by-side) */}
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
              try { logEvent("ui.memory.query_decision", { decision_ref: slug }); } catch { /* ignore */ }
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
        {(isStreaming || shortAnswer) && (
          <ShortAnswer
            isStreaming={isStreaming}
            tokens={tokens as any}
            mainAnswer={mainAnswer}
            leadBeforeEvents={leadBeforeEvents || shortAnswer}
            eventsFromShort={eventsFromShort}
            anchorHeading={anchorHeading}
            anchorDescription={anchorDescription}
            nextFromShort={nextFromShort}
            nextTitle={nextTitle}
            receipts={receipts}
            onCitationClick={handleCitationClick}
            onOpenAllowedIds={handleOpenAllowedIds}
            onOpenAudit={handleOpenAudit}
          />
        )}
        {/* All Allowed IDs (expandable enriched catalog) */}
        {!isStreaming && finalData && (
          <AllowedIdsDrawer
            anchorId={finalData?.anchor?.id || ""}
            edges={(
              ((finalData as any)?.graph?.edges) ||
              ((finalData as any)?.evidence?.graph?.edges) || []
            ) as GraphEdge[]}
            anchor={(finalData?.anchor || (finalData as any)?.evidence?.anchor) as any}
            allowedIds={finalData?.meta?.allowed_ids || finalData?.evidence?.allowed_ids || []}
            policyFp={finalData?.meta?.policy_fp || ""}
            allowedIdsFp={finalData?.meta?.allowed_ids_fp || ""}
            snapshotEtag={finalData?.meta?.snapshot_etag || ""}
            loadCatalog={loadCatalog}
            loading={catalogLoading}
            error={catalogError}
          />
        )}

        {/* Evidence & graph sections */}
        {!isStreaming && finalData?.evidence && (
          <div className="mt-8 space-y-6">
            {filteredEvidenceItems.length > 0 ? (
              <>
                {/* Section: Evidence */}
                <div className="mt-6">
                  <div className="section-hairline" />
                  <div className="flex items-center justify-between">
                    <h3 className="text-xs tracking-widest uppercase text-neonCyan/80">Evidence</h3>
                    <div className="flex items-center gap-3">
                      <Button onClick={handleDownloadEvidence} variant="secondary" className="text-xs">
                        Download evidence
                        {typeof finalData?.meta?.bundle_size_bytes === "number" ? (
                          <span className="ml-2 opacity-70">
                            ({Math.max(1, Math.round(finalData.meta.bundle_size_bytes / 1024))} KB)
                          </span>
                        ) : null}
                      </Button>
                    </div>
                  </div>
                  <div className="sticky top-0 bg-surface/80 backdrop-blur py-2 z-10 mt-4">
                    {Object.keys(tagCounts).length > 0 ? (
                      <TagFilter
                        maxRows={2}
                        tags={tagCounts}
                        selected={selectedTags}
                        onToggle={(tag) => {
                          toggleTag(tag);
                          try { logEvent("ui.tag_toggle", { tag, rid: finalData?.meta?.request_id ?? null }); } catch {}
                        }}
                      />
                    ) : (
                      <p className="text-copy text-xs italic">No tags found in this evidence bundle.</p>
                    )}
                  </div>
                </div>
                <EvidenceList
                  items={filteredEvidenceItems}
                  anchorId={anchorId}
                  selectedId={selectedEvidenceId}
                  onSelect={(id) => {
                    setSelectedEvidenceId(id);
                    try {
                      logEvent("ui.evidence_select", { id, rid: finalData?.meta?.request_id ?? null });
                    } catch {}
                  }}
                  className="mt-2"
                />
              </>
            ) : (
              <EmptyResult
                heading="Unknown decision reference."
                message="That slug isn’t in the vault. Try the Natural path and just ask your question."
                ctaLabel="Go to Natural"
                onCta={switchToNatural}
                details='Tip: Ask something like “Why did Panasonic exit plasma TV production?”'
              />
            )}
          </div>
        )}
        {/* Error state */}
        {error && !isStreaming && (
          <div className="mt-6 pt-4 border-t border-red-500">
            <p className="text-red-400 font-mono text-sm">Error: {String(error)}</p>
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
          meta={finalData.meta}
          evidence={finalData.evidence}
          answer={finalData.answer}
          bundle_url={finalData.bundle_url}
        />
      )}
    </div>
  );
}
