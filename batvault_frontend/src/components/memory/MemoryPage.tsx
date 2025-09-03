import React, { useMemo, useState, useEffect, useCallback, useRef } from "react";
import Card from "./ui/Card";
import QueryPanel from "./QueryPanel";
import EvidenceList from "./EvidenceList";
import { useMemoryAPI } from "../../hooks/useMemoryAPI";
import type { EvidenceItem, EvidenceBundle } from "../../types/memory";
import AuditDrawer from "./AuditDrawer";
import Button from "./ui/Button";
import { logEvent } from "../../utils/logger";
import GraphView from "./GraphView";
import TagFilter from "./ui/TagFilter";
import { useAliasResolver } from "../../hooks/useAliasResolver";
import EmptyResult from "./EmptyResult";

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
  } = useMemoryAPI();

  // --- UI-only validation message just below the QueryPanel input ---
  const [emptyHint, setEmptyHint] = useState<string | null>(null);
  const queryPanelHostRef = useRef<HTMLDivElement>(null);

  // Hook to resolve decision slugs to human‑friendly titles. Cached
  // internally to avoid repeated fetches.
  const resolveAlias = useAliasResolver();

  // Store the resolved next decision title (from succeeding transition)
  const [nextTitle, setNextTitle] = useState<string | undefined>(undefined);

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

  // Strategic structured logging for audit drawer interactions.
  const handleOpenAudit = () => {
    setAuditOpen(true);
    // Deterministic event key; include request id if present.
    logEvent("ui.audit_open", { rid: finalData?.meta?.request_id ?? null });
  };

  const rawShortAnswer = finalData?.answer?.short_answer;
  // Render-time split: if the short answer (or streaming tokens) contains
  // an inline "Next:" sentence, render it on a new line with a spacer.
  const streamingText = isStreaming ? tokens.join("") : undefined;
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
  const shortAnswer = useMemo(() => {
    if (!rawShortAnswer) return undefined;
    try {
      const parts = String(rawShortAnswer).split(/(?<=[.!?])\s+/).slice(0, 2);
      return parts.join(" ");
    } catch { return rawShortAnswer; }
  }, [rawShortAnswer]);

  // Compute the slug/id of the next succeeding transition (if any). Prefer
  // the `to` field if present, otherwise fall back to the transition id.
  const nextSlug: string | undefined = useMemo(() => {
    const nxt: any = (finalData as any)?.evidence?.transitions?.succeeding?.[0];
    if (!nxt) return undefined;
    return (nxt.to as string) || (nxt.id as string) || undefined;
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

  // When the slug changes, resolve the human title via the alias resolver.
  useEffect(() => {
    let ignore = false;
    (async () => {
      if (nextSlug) {
        const title = await resolveAlias(nextSlug);
        if (!ignore) setNextTitle(title);
      } else {
        setNextTitle(undefined);
      }
    })();
    return () => {
      ignore = true;
    };
  }, [nextSlug, resolveAlias]);
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
          id: finalData?.evidence?.anchor?.id ?? null,
        });
      } catch {
        /* ignore logging errors */
      }
    }
  }, [finalData]);

  // Extract supplementary answer fields for UI enhancements
  const citationIds: string[] = finalData?.answer?.supporting_ids ?? [];
  const fallbackUsed: boolean | undefined = finalData?.meta?.fallback_used;
  // Derive a label describing the path taken (deterministic fallback or model).
  const pathLabel: string = fallbackUsed ? "Deterministic" : "Model";
  // nextLabel has been superseded by alias resolution via `nextTitle`. See
  // useEffect below for details.

  // Log when anchor maker/date chips are present
  // Fire a layout painted event exactly once when this component mounts. When
  // finalData is available we include its request id in the payload, otherwise
  // pass null. This uses an empty dependency array so it runs only on the
  // initial render.
  useEffect(() => {
    try {
      logEvent("ui.memory.layout_painted", { rid: finalData?.meta?.request_id ?? null });
    } catch {
      /* ignore logging errors */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Handler for evidence bundle download.
  // Strategy:
  // 1) If we have a request id, use the canonical presign flow:
  //    POST /v2/bundles/{rid}/download → { url }, then open the url
  // 2) Else, if the response included a bundle_url, open that
  // 3) Else, as a last resort (rid only), GET /v2/bundles/{rid} and save blob
  const handleDownloadEvidence = useCallback(async () => {
    try { logEvent("ui.evidence_download_click", { rid: finalData?.meta?.request_id ?? null }); } catch { /* ignore */ }
    const bundleUrl = (finalData as any)?.bundle_url as string | undefined;
    const ridFromMeta = (finalData as any)?.meta?.request_id as string | undefined;
    let rid = ridFromMeta;
    if (!rid && bundleUrl) {
      const m = /\/v2\/bundles\/([^\/\s]+)/i.exec(bundleUrl);
      if (m && m[1]) rid = m[1];
    }
    // Delegate to shared utility (presigned-first with safe fallbacks)
    const { openEvidenceBundle } = await import("../../utils/bundle");
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

  const transitionsBoth = useMemo(() => {
    const t = finalData?.evidence?.transitions as any;
    const preceding = Array.isArray(t?.preceding) ? t.preceding.map((x: any) => ({ ...x, type: 'TRANSITION' as const })) : [];
    const succeeding = Array.isArray(t?.succeeding) ? t.succeeding.map((x: any) => ({ ...x, type: 'TRANSITION' as const })) : [];
    return { preceding, succeeding };
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
    if (transitionsBoth.preceding?.length) items.push(...transitionsBoth.preceding);
    if (rankedEvents?.length) items.push(...rankedEvents);
    if (transitionsBoth.succeeding?.length) items.push(...transitionsBoth.succeeding);
    return items;
  }, [anchorItem, transitionsBoth, rankedEvents]);

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
  // Order: anchor → all supporting_ids (no cap).
  const receipts: string[] = useMemo(() => {
    const ids = new Set<string>();
    if (anchorId) ids.add(anchorId); // anchor from evidence
    (finalData?.answer?.supporting_ids ?? []).forEach((id: string) => ids.add(id));
    return Array.from(ids);
  }, [anchorId, finalData]);

  // Cache of resolved human titles for receipt chips: slug -> title|undefined
  const [receiptTitles, setReceiptTitles] = useState<Record<string, string | undefined>>({});

  // Resolve human titles for current receipts (anchor/supporting/events)
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const updates: Record<string, string | undefined> = {};
      for (const id of receipts) {
        if (!(id in receiptTitles)) {
          const t = await resolveAlias(id);
          updates[id] = t;
          if (!t) {
            try { logEvent("ui.receipt_title_fallback", { id, rid: finalData?.meta?.request_id ?? null }); } catch {}
          }
        }
      }
      if (!cancelled && Object.keys(updates).length) {
        setReceiptTitles((prev) => ({ ...prev, ...updates }));
      }
    })();
    return () => { cancelled = true; };
  }, [receipts, resolveAlias]);

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
            onAsk={(intent: string, decisionRef: string) => {
              const slug = decisionRef?.trim();
              if (!slug) {
                setEmptyHint("I can’t trace the void — drop a decision id.");
                return;
              }
              setEmptyHint(null);
              const effIntent = intent ?? "why_decision";
              try { logEvent("ui.memory.ask", { intent: effIntent, decision_ref: slug }); } catch { /* ignore */ }
              return ask(effIntent, slug);
            }}
            onQuery={(q: string) => {
              if (emptyHint) setEmptyHint(null);
              return query(q);
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
          <div className="mt-6">
            <div className="section-hairline" />
            <div>
              <div className="flex items-center justify-between">
                <h3 className="text-xs tracking-widest uppercase text-neonCyan/80">Short answer</h3>
                <span
                  className="text-xs font-mono px-2 py-1 rounded-md border border-gray-600 text-copy/80 whitespace-nowrap self-start"
                  title={fallbackUsed ? "Deterministic fallback path used" : "Model path used"}
                >
                  Path: {pathLabel}
                </span>
              </div>
              <div className="mt-1">
                <p className="text-copy text-lg md:text-xl leading-snug">
                  {mainAnswer || (isStreaming ? tokens.join("") : shortAnswer)}
                </p>
                {(nextFromShort || nextTitle) && (
                  <p className="text-copy/80 text-lg md:text-xl leading-snug mt-4">
                    <span className="font-semibold">Next:</span>{" "}{nextFromShort ?? nextTitle}
                  </p>
                )}
              </div>
              <div className="section-hairline my-3" />
               {/* Supporting IDs moved lower */}
              {receipts && receipts.length > 0 && (
                <div className="mt-3 flex gap-2 overflow-x-auto whitespace-nowrap no-scrollbar">
                  {receipts.map((cid) => (
                    <button
                      key={cid}
                      type="button"
                      onClick={() => {
                        try {
                          logEvent("ui.citation_click", {
                            id: cid,
                            rid: finalData?.meta?.request_id ?? null,
                          });
                        } catch { /* ignore logging errors */ }
                        setSelectedEvidenceId(cid);
                        const el = document.getElementById(`evidence-${cid}`);
                        if (el) {
                          el.scrollIntoView({ behavior: "smooth", block: "center" });
                          el.classList.add("animate-pulse");
                          window.setTimeout(() => el.classList.remove("animate-pulse"), 1200);
                        }
                      }}
                      className="text-xs px-2 py-0.5 rounded-full border border-vaultred/50 text-vaultred hover:bg-vaultred/30 transition-colors"
                    >
                      <span
                        title={receiptTitles[cid] ? undefined : "No human title available"}
                        className={receiptTitles[cid] ? "font-sans" : "font-mono"}
                      >
                        {receiptTitles[cid] ?? cid}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
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
                {/* Section: Graph */}
                <div className="mt-8">
                  <div className="section-hairline" />
                  <h3 className="text-xs tracking-widest uppercase text-neonCyan/80 mb-2">Graph</h3>
                  <GraphView
                    items={filteredEventsForGraph}
                    anchor={finalData?.evidence?.anchor as any}
                    transitions={finalData?.evidence?.transitions as any}
                    selectedId={selectedEvidenceId}
                    onSelect={(id) => {
                      setSelectedEvidenceId(id);
                      try {
                        logEvent("ui.evidence_select", { id, rid: finalData?.meta?.request_id ?? null });
                      } catch {}
                    }}
                  />
                  {/* Transitions summary (preceding/succeeding) */}
                </div>
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
        {/* Audit drawer trigger */}
        {(tokens.length > 0 || isStreaming || finalData) && (
          <div className="mt-8 flex justify-end">
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
          <div className="mt-6 pt-4 border-t border-red-500">
            <p className="text-red-400 font-mono text-sm">Error: {error.message}</p>
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
