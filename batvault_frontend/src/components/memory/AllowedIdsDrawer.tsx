import React, { useMemo, useState } from "react";
import type { EnrichedNode, GraphEdge, EvidenceItem } from "../../types/memory";
import Button from "./ui/Button";
import GraphView from "./GraphView";
import { normalizeErrorMessage } from "../../utils/errors";
import BaseDrawer from "./ui/BaseDrawer";
import Section from "./ui/Section";
import Badge from "./ui/Badge";

export interface AllowedIdsDrawerProps {
  anchorId: string;
  allowedIds: string[];
  policyFp: string;
  allowedIdsFp: string;
  snapshotEtag: string;
  loadCatalog: (args: { anchorId: string; snapshotEtag: string; allowedIds: string[]; cacheKey: string }) => Promise<{ itemsById: Record<string, EnrichedNode>, fromCache: boolean }>;
  loading: boolean;
  error: string | null;
  /** Oriented edges for the current scope (v3 edges-only) */
  edges?: GraphEdge[];
  /** Anchor node (policy-masked) for graph centering */
  anchor?: EvidenceItem;
}

export default function AllowedIdsDrawer(props: AllowedIdsDrawerProps) {
  const { anchorId, allowedIds, policyFp, allowedIdsFp, snapshotEtag, loadCatalog, loading, error, edges = [], anchor } = props;
  const [open, setOpen] = useState(false);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [rawModeById, setRawModeById] = useState<Map<string, 'wire' | 'item'>>(new Map());
  const [itemsById, setItemsById] = useState<Record<string, EnrichedNode>>({});
  // Local selection state for the embedded graph
  const [selectedGraphId, setSelectedGraphId] = useState<string | undefined>(undefined);
  const cacheKey = useMemo(() => `${snapshotEtag}|${allowedIdsFp}|${policyFp}`, [snapshotEtag, allowedIdsFp, policyFp]);

  async function openAndLoad() {
    setOpen(true);
    if (Object.keys(itemsById).length === 0) {
      try {
        const { itemsById: map } = await loadCatalog({ anchorId, snapshotEtag, allowedIds, cacheKey });
        setItemsById(map);
      } catch (_e) { /* handled via props.error */ }
    }
  }
  const close = () => setOpen(false);

  // Open-on-demand signal from outside (short-answer button).
  React.useEffect(() => {
    function onOpenSignal() {
      if (!open) void openAndLoad();
    }
    window.addEventListener('open-allowed-ids', onOpenSignal);
    return () => window.removeEventListener('open-allowed-ids', onOpenSignal);
  }, [open, anchorId, snapshotEtag, allowedIds, cacheKey, itemsById, loadCatalog]);

 
  // Deterministic alias-event set from edges (no heuristics)
  const aliasEventIds = useMemo(() => {
    const s = new Set<string>();
    (edges || []).forEach((e) => {
      if (!e) return;
      if ((e as any).type === "ALIAS_OF" && typeof (e as any).from === "string") {
        s.add(String((e as any).from));
      }
    });
    return s;
  }, [edges]);

  const groups = useMemo(() => {
    const nodes: EnrichedNode[] = [];
    allowedIds.forEach((id) => {
      const n = itemsById[id];
      if (n) {
        nodes.push(n);
      } else {
        // UI-only fallback object: do not parse/construct anchors for logic.
        // We refrain from hand-rolling domain parsing; display-only hint:
        let domain = "";
        const hashIdx = typeof id === "string" ? id.indexOf("#") : -1;
        if (hashIdx > 0) {
          // Safe substring for UX label only; never used to build anchors.
          domain = id.slice(0, hashIdx);
        }
        nodes.push({
          id,
          type: id.includes("#e-") ? "EVENT" : "DECISION",
          domain,
          title: id
        });
      }
    });
    const toDate = (s?: string) => (s ? Date.parse(s) || 0 : 0);
    nodes.sort((a, b) => {
      if (a.type !== b.type) return a.type === "EVENT" ? -1 : 1;
      const ta = toDate(a.timestamp); const tb = toDate(b.timestamp);
      if (ta !== tb) return tb - ta;
      return a.id.localeCompare(b.id);
    });
    return {
      events: nodes.filter(n => n.type === "EVENT"),
      decisions: nodes.filter(n => n.type === "DECISION"),
    };
  }, [allowedIds, itemsById]);

  const ordered = useMemo(
    () => [...groups.events, ...groups.decisions],
    [groups.events, groups.decisions]
  );

  const errorMsg = error ? normalizeErrorMessage(error) : null;

  const toggleRow = (id: string) => {
    setExpandedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  
  const renderRow = (n: EnrichedNode) => {
    const isOpen = expandedIds.has(n.id);
    return (
      // Node card (darker)
      <div key={n.id} className="p-3 mb-2 rounded-xl border border-white/5 bg-black/40">
        <div
          role="button"
          tabIndex={0}
          onClick={() => toggleRow(n.id)}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && toggleRow(n.id)}
          className="flex items-center justify-between cursor-pointer select-none"
        >
          <div className="flex items-center gap-2">
            {/* Type badge */}
            <span
              className={`text-[10px] px-2 py-0.5 rounded-full border ${
                n.type === "EVENT"
                  ? "bg-neonCyan/10 text-neonCyan border-neonCyan/40"    /* blue */
                  : "bg-amber-500/10 text-amber-300 border-amber-400/40" /* orange */
              }`}
            >
              {n.type}
            </span>
            {/* Anchor / Alias chips */}
            {n.id === anchorId ? (
              <span className="text-[10px] px-2 py-0.5 rounded-full border border-white/20 bg-white/10">ANCHOR</span>
            ) : null}
            {aliasEventIds.has(n.id) ? (
              <span className="text-[10px] px-2 py-0.5 rounded-full border border-white/20 bg-white/5">ALIAS EVENT</span>
            ) : null}
            {/* ID pill — match short-answer dark red badges */}
            <Badge size="xs" variant="dangerOutline">{n.id}</Badge>
          </div>
          <span className="text-xs opacity-70">{isOpen ? "Collapse" : "Details"}</span>
        </div>
        <div className="mt-1 text-sm">
          <div className="font-medium">{n.title || n.id}</div>
          {n.timestamp ? <div className="text-xs opacity-70">{n.timestamp}</div> : null}
        </div>
        {isOpen && (
          <div className="mt-2 bg-black/30 rounded p-3 text-sm">
            {/* Raw JSON only (with mode toggle) */}
            <div className="flex items-center gap-2 text-[11px]">
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); setRawModeById(prev => new Map(prev).set(n.id, 'wire')); }}
                className={`underline ${(rawModeById.get(n.id) ?? 'wire') === 'wire' ? 'opacity-100' : 'opacity-70 hover:opacity-100'}`}
              >
                Wire (masked node only)
              </button>
              <span className="opacity-30">|</span>
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); setRawModeById(prev => new Map(prev).set(n.id, 'item')); }}
                className={`underline ${(rawModeById.get(n.id) ?? 'wire') === 'item' ? 'opacity-100' : 'opacity-70 hover:opacity-100'}`}
              >
                Item (masked + mask_summary)
              </button>
            </div>
            <pre className="mt-2 text-[11px] leading-tight bg-black/30 p-2 rounded overflow-auto">
              {(() => {
                const mode = (rawModeById.get(n.id) ?? 'wire');
                if (mode === 'item') return JSON.stringify(n, null, 2);
                const { mask_summary, ...wire } = (n as any) || {};
                return JSON.stringify(wire, null, 2);
              })()}
            </pre>
          </div>
        )}
      </div>
    );
  };

  return (
    <BaseDrawer open={open} onClose={close} id="allowed-ids-drawer" testId="allowed-ids-drawer" placement="bottom" inline className="mt-6">
      <div className="p-6">
        <Section
          title="All Allowed IDs"
          count={allowedIds.length}
          right={
            <div className="flex items-center gap-3">
              {errorMsg ? <span className="text-vaultred text-xs mr-2">{errorMsg}</span> : null}
              <Button onClick={close} variant="secondary" className="text-xs">Close</Button>
            </div>
          }
        />
        {/* Allowed IDs list */}
        <div className="mt-3 bg-white/5 rounded">
          {loading && Object.keys(itemsById).length === 0 ? (
            <div className="p-3 text-xs opacity-70">Loading catalog…</div>
          ) : (
            <>{ordered.map(renderRow)}</>
          )}
        </div>

        {/* Graph — edges are already oriented in v3 */}
        {Array.isArray(edges) && edges.length > 0 ? (
          <>
            <div className="mt-6">
              <Section title="Graph" />
            </div>
            <div className="mt-3 bg-white/5 rounded p-3">
              <GraphView
                catalog={itemsById}
                items={[]}
                anchor={anchor as any}
                edges={edges as any}
                selectedId={selectedGraphId}
                onSelect={(id) => setSelectedGraphId(id)}
              />
            </div>
          </>
        ) : null}
      </div>
    </BaseDrawer>
  );
}