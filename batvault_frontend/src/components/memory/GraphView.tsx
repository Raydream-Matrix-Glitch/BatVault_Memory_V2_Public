import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import CytoscapeComponent from "react-cytoscapejs";
import cytoscape from "cytoscape";
import coseBilkent from "cytoscape-cose-bilkent";
import { logEvent } from "../../utils/logger";
import {
  GRAPH_STYLESHEET,
  GRAPH_LAYOUT,
  DECISION_BORDER, EVENT_BORDER,
  EDGE_CAUSAL, EDGE_LED_TO, EDGE_ALIAS_OF
} from "./graph/config";
 import type { GraphEdge, EnrichedNode } from "../../types/memory";

// Narrow the edge type locally to express server orientation intent.
type OrientedGraphEdge = GraphEdge & { orientation?: "preceding" | "succeeding" };

// Register the cose-bilkent layout once.
try { cytoscape.use(coseBilkent); } catch { /* no-op */ }

export interface GraphViewProps {
  /** Evidence items (typically events) */
  items: Array<{ id: string; timestamp?: string | number }>;
  /** Anchor decision (optional but recommended) */
  anchor?: { id: string; timestamp?: string | number };
  /** Oriented graph edges (V3) */
  edges?: OrientedGraphEdge[];
  /** Optional Enriched Catalog: id -> masked node (titles/types) */
  catalog?: Record<string, EnrichedNode>;
  /** Currently selected evidence id */
  selectedId?: string;
  /** Selection handler */
  onSelect: (id: string) => void;
}

// --- Anchor-centering helpers (deterministic) ---
function chooseAnchorNode(cy: cytoscape.Core): cytoscape.NodeSingular | null {
  const nodes = cy.nodes();
  if (nodes.length === 0) return null;
  // 1) explicit flag
  const flagged = nodes.filter((n) => !!n.data("is_anchor"));
  if (flagged.length > 0) return flagged[0];
  // 2) highest-degree decision
  const decisions = nodes.filter("[kind = 'decision']");
  if (decisions.length > 0) {
    let best = decisions[0];
    let bestDeg = best.degree(false);
    decisions.forEach((n) => {
      const d = n.degree(false);
      if (d > bestDeg) { best = n; bestDeg = d; }
    });
    return best;
  }
  // 3) highest-degree any
  let best = nodes[0];
  let bestDeg = best.degree(false);
  nodes.forEach((n) => {
    const d = n.degree(false);
    if (d > bestDeg) { best = n; bestDeg = d; }
  });
  return best;
}

function presetRadialPositions(cy: cytoscape.Core) {
  if (!cy || cy.destroyed()) return;
  const all = cy.nodes();
  if (all.length === 0) return;

  const anchor = chooseAnchorNode(cy);
  if (!anchor) return;

  const w = Math.max(cy.width(), 600);
  const h = Math.max(cy.height(), 400);
  const cx = w / 2;
  const cyy = h / 2;
  const radius = Math.min(w, h) * 0.45;

  // Partition nodes: decisions that precede/succeed the anchor vs everything else (incl. events)
  const edgesToAnchor   = cy.edges(`[target = "${anchor.id()}"]`);
  const edgesFromAnchor = cy.edges(`[source = "${anchor.id()}"]`);
  const precedingDecisionIds = new Set<string>(
    edgesToAnchor
    .filter('[label = "CAUSAL"]')
    .filter('[orientation = "preceding"]')
    .connectedNodes()
    .filter((n: cytoscape.NodeSingular): boolean =>
      n.id() !== anchor.id() && n.data('kind') === 'decision')
    .map((n) => n.id()));
  const succeedingDecisionIds = new Set<string>(
    edgesFromAnchor
    .filter('[label = "CAUSAL"]')
    .filter('[orientation = "succeeding"]')
    .connectedNodes()
    .filter((n: cytoscape.NodeSingular): boolean =>
      n.id() !== anchor.id() && n.data('kind') === 'decision')
    .map((n) => n.id()));

  const leftDecisions =
    all.filter((n: cytoscape.NodeSingular): boolean => precedingDecisionIds.has(n.id())) as cytoscape.Collection<cytoscape.NodeSingular>;
  const rightDecisions =
    all.filter((n: cytoscape.NodeSingular): boolean => succeedingDecisionIds.has(n.id())) as cytoscape.Collection<cytoscape.NodeSingular>;
  const others =
    (all.not(anchor).not(leftDecisions).not(rightDecisions)) as cytoscape.Collection<cytoscape.NodeSingular>;

  const placeColumn = (nodes: cytoscape.Collection<cytoscape.NodeSingular>, x: number): void => {
    if (nodes.length === 0) return;
    const count = nodes.length;
    const stepY = Math.min(120, Math.max(80, (h * 0.65) / (count + 1)));
    const startY = cyy - (stepY * (count - 1)) / 2;
    nodes.forEach((n: cytoscape.NodeSingular, i: number) => {
      n.position({ x, y: startY + i * stepY });
    });
  };

  cy.batch(() => {
    // Center the anchor
    anchor.position({ x: cx, y: cyy });

    // Left = preceding decisions, Right = succeeding decisions
    placeColumn(leftDecisions,  cx - radius);
    placeColumn(rightDecisions, cx + radius);

    // Arrange remaining nodes (mostly events) on a ring
    if (others.length > 0) {
      const count = others.length;
      const step = (2 * Math.PI) / count;
      const start = -Math.PI / 2; // start from top
      others.forEach((n: cytoscape.NodeSingular, i: number) => {
        const angle = start + i * step;
        n.position({
          x: cx + radius * Math.cos(angle),
          y: cyy + radius * Math.sin(angle),
        });
      });
    }
  });
  // keep center stable during layout; unlock after in layoutstop handler
  anchor.lock();
  try {
    (window as any)?.logEvent?.("ui.graph.preset_layout", {
      anchor_id: anchor.id(),
      nodes_total: all.length,
      left_decisions: leftDecisions.length,
      right_decisions: rightDecisions.length,
      ring_count: others.length,
    });
  } catch { /* no-op */ }
}

// After layout: separate overlapping domain compounds (simple horizontal pack)
function resolveDomainOverlaps(cy: cytoscape.Core) {
  const margin = 32; // px gap between domain boxes
  const doms = cy.nodes("[kind = 'domain']");
  if (doms.length <= 1) return;
  // run a few passes; shifting one box can cause a new overlap
  for (let pass = 0; pass < 4; pass++) {
    let moved = false;
    const items = doms.map((n) => ({ n, bb: n.boundingBox() }))
                      .sort((a, b) => a.bb.x1 - b.bb.x1);
    for (let i = 1; i < items.length; i++) {
      const A = items[i - 1], B = items[i];
      const overlapX = Math.min(A.bb.x2, B.bb.x2) - Math.max(A.bb.x1, B.bb.x1);
      const overlapY = Math.min(A.bb.y2, B.bb.y2) - Math.max(A.bb.y1, B.bb.y1);
      if (overlapX > -margin && overlapY > -margin) {
        const shift = (A.bb.x2 + margin) - B.bb.x1;
        const pos = B.n.position();
        B.n.position({ x: pos.x + shift, y: pos.y });
        moved = true;
      }
    }
    if (!moved) break;
  }
}

/**
 * GraphView (V3):
 * - Decision node (anchor) in the center
 * - Event nodes around it (edge: EVENT -> DECISION when LED_TO or supported_by)
 * - Decision relations via oriented edges:
 *     CAUSAL (preceding/succeeding), LED_TO (arrows), ALIAS_OF (neutral, dashed)
 */
export default function GraphView({
  items,
  anchor,
  selectedId,
  edges,
  catalog,
  onSelect,
}: GraphViewProps) {
  // Keep latest onSelect without rebinding Cytoscape events
  const onSelectRef = useRef(onSelect);
  useEffect(() => { onSelectRef.current = onSelect; }, [onSelect]);

  // Dev-only guard: if we have CAUSAL edges but no orientation, we likely fell back to bundle edges.
  useEffect(() => {
  // Vite doesn’t shim `process` in the browser; use `import.meta.env.MODE`.
  const __DEV__ = (typeof import.meta !== "undefined" && (import.meta as any).env?.MODE !== "production");
  if (__DEV__ && Array.isArray(edges) && edges.length) {
      const hasCausal = edges.some(e => e?.type === "CAUSAL");
      const hasOrientation = edges.some(e => e?.type === "CAUSAL" && (e as OrientedGraphEdge).orientation);
      if (hasCausal && !hasOrientation) {
        // eslint-disable-next-line no-console
        console.warn("GraphView: CAUSAL edges without orientation; rendering fallback layout.");
      }
    }
  }, [edges]);
  const elements = useMemo(() => {
    const els: any[] = [];
    const nodeIds = new Set<string>();

    // Precompute type hints from edges and items (deterministic sets).
    const eventIds = new Set<string>();
    const decisionIds = new Set<string>();
    // Track domain membership (edge domains + preferred catalog node domains)
    const domainMembers: Record<string, Set<string>> = {};
    const ensureDomain = (name: string) => {
      const key = String(name || "Unknown");
      if (!domainMembers[key]) domainMembers[key] = new Set<string>();
      return domainMembers[key];
    };

    // --- Unique domain color allocator (random-ish but non-repeating) ---
    const usedHues: number[] = [];
    const HUE_SEP = 24;        // minimum degrees between hues
    const GOLDEN = 137.508;    // golden-angle step to avoid collisions
    const hashHue = (s: string) => {
      let h = 0; for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
      return Math.abs(h) % 360;
    };
    const angleDist = (a: number, b: number) => {
      const d = Math.abs(a - b) % 360; return d > 180 ? 360 - d : d;
    };
    const pickDomainColor = (name: string): string => {
      let hue = hashHue(name || `${Math.random()}`);
      let tries = 0, maxTries = Math.ceil(360 / HUE_SEP);
      while (tries < maxTries && usedHues.some(h => angleDist(h, hue) < HUE_SEP)) {
        hue = (hue + GOLDEN) % 360;
        tries++;
      }
      if (tries >= maxTries) return "#ffffff"; // fallback if we saturate the wheel
      usedHues.push(hue);
      return `hsl(${hue} 70% 55%)`;
    };

    (Array.isArray(edges) ? edges : []).forEach((e) => {
      if (!e || !e.from || !e.to) return;
      if (e.type === "LED_TO" || e.type === "ALIAS_OF") {
        eventIds.add(e.from);
        decisionIds.add(e.to);
      } else if (e.type === "CAUSAL") {
        decisionIds.add(e.from);
        decisionIds.add(e.to);
      }
    });
    (Array.isArray(items) ? items : []).forEach((ev) => { if (ev?.id) eventIds.add(ev.id); });
    if (anchor?.id) decisionIds.add(anchor.id);

    const labelFor = (id: string): { label: string; year?: string } => {
      const fromCatalog = (catalog && (catalog as any)[id]) || null;
      const title = fromCatalog?.title || fromCatalog?.description || null;
      const ts = fromCatalog?.timestamp || (items.find(i=>i.id===id)?.timestamp) || (anchor?.id===id ? (anchor as any)?.timestamp : undefined);
      let year: string | undefined;
      if (ts) { try { year = String(new Date(ts).getUTCFullYear()); } catch { /* ignore */ } }
      const label = String(title || id);
      return { label, year };
    };

    const kindFor = (id: string): "decision" | "event" => {
      const fromCatalog = (catalog && (catalog as any)[id]) || null;
      const t = (fromCatalog?.type || fromCatalog?.kind || "").toString().toUpperCase();
      if (t === "EVENT") return "event";
      if (t === "DECISION") return "decision";
      if (eventIds.has(id)) return "event";
      return "decision";
    };

    const addNode = (id: string, extra?: Record<string, any>) => {
      if (!id || nodeIds.has(id)) return;
      nodeIds.add(id);
      const { label, year } = labelFor(id);
      const kind = kindFor(id);
      const kindLabel = kind === "decision" ? "DECISION" : "EVENT";
      const flags: string[] = [];
      if (extra && extra.is_anchor) flags.push("ANCHOR");
      if (extra && extra.is_alias)  flags.push("ALIAS EVENT");
      const display = [kindLabel, ...flags, label, year].filter(Boolean).join("\n");
      els.push({ data: { id, label: display, kind, ...(extra || {}) } });
    };

    const addEdge = (src: string, dst: string, label?: string, edgeId?: string, orientation?: "preceding" | "succeeding") => {
      if (!src || !dst) return;
      const id = edgeId || `${src}->${dst}${label ? `__${label}` : ""}`;
      if (els.find((e) => e.data && e.data.id === id)) return; // de-dupe
      // Flag cross-domain edges when both endpoints have different node domains.
      const srcDom = (catalog as any)?.[src]?.domain;
      const dstDom = (catalog as any)?.[dst]?.domain;
      const cross = !!(srcDom && dstDom && srcDom !== dstDom);
      els.push({ data: { id, source: src, target: dst, label, cross, orientation } });
    };

    // Add anchor first for deterministic centering
    if (anchor?.id) addNode(anchor.id, { is_anchor: true });

    // Add all endpoints from edges with correct node kinds
    (Array.isArray(edges) ? edges : []).forEach((e) => {
      if (!e || !e.from || !e.to) return;
      const label =
        e.type === "CAUSAL" ? "CAUSAL" :
        e.type === "LED_TO" ? "LED_TO" :
        e.type === "ALIAS_OF" ? "ALIAS_OF" : undefined;

      if (e.type === "LED_TO" || e.type === "ALIAS_OF") {
        addNode(e.from, { is_alias: e.type === "ALIAS_OF" }); // event
        addNode(e.to);   // decision
      } else {
        addNode(e.from); // decision
        addNode(e.to);   // decision
      }
      addEdge(e.from, e.to, label, `${e.from}->${e.to}__${label || "EDGE"}`, (e as any)?.orientation);
      if (e.domain) {
        ensureDomain(String(e.domain)).add(e.from);
        ensureDomain(String(e.domain)).add(e.to);
      }
    });

    // Back-compat: render provided items (events) even if not present in edges.
    (Array.isArray(items) ? items : []).forEach((ev) => { if (ev?.id) addNode(ev.id); });

    // ✅ Prefer node domains from the Enriched Catalog (source of truth)
    Array.from(nodeIds).forEach((nid) => {
      const dom = (catalog as any)?.[nid]?.domain;
      if (dom) ensureDomain(String(dom)).add(nid);
    });

    // Create compound domain nodes and assign parents
    Object.entries(domainMembers).forEach(([dom, ids]) => {
      const domId = `domain:${dom}`;
      // Add domain parent node with a unique, non-repeating color
      els.push({ data: { id: domId, label: dom, kind: "domain", domainColor: pickDomainColor(dom) } });
      // Assign each member node to this domain as its parent
      ids.forEach((nid) => {
        const found = els.find((e) => e?.data && (e as any).data.id === nid && !(e as any).data.source);
        if (found && (found as any).data) {
          (found as any).data.parent = domId;
        }
      });
    });

    try {
      logEvent("ui.graph.catalog_applied", {
        catalog: catalog ? Object.keys(catalog).length : 0,
        node_count: nodeIds.size,
        edge_count: (Array.isArray(edges) ? edges.length : 0)
      });
    } catch { /* no-op */ }

    return els;
  }, [items, anchor?.id, edges, catalog]);

  // Build a legend from actual domains present
  const domainLegend = useMemo(() => {
    const rows: Array<{ name: string; color: string }> = [];
    (elements as any[]).forEach((e: any) => {
      const d = e?.data;
      if (d?.kind === "domain" && d?.label && d?.domainColor) {
        rows.push({ name: d.label, color: d.domainColor });
      }
    });
    // unique + sorted for stability
    return Array.from(new Map(rows.map(r => [r.name, r])).values())
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [elements]);

  // --- Expand overlay state ---
  const [isOverlayOpen, setIsOverlayOpen] = useState(false);
  const [canPortal, setCanPortal] = useState(false);
  useEffect(() => { setCanPortal(true); }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setIsOverlayOpen(false);
    };
    if (isOverlayOpen) window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOverlayOpen]);
  // Lock body scroll while the overlay is open (true viewport modal)
  useEffect(() => {
    if (!canPortal) return;
    if (isOverlayOpen) {
      const prev = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      return () => { document.body.style.overflow = prev; };
    }
  }, [isOverlayOpen, canPortal]);

  // Layout config (centralized)
  const defaultLayout = GRAPH_LAYOUT;

  // Style overrides that are safe to append after GRAPH_STYLESHEET.
  // We make domain backgrounds more visible and move their labels INSIDE.
  const computedStyles = useMemo(() => {
    const overrides: any[] = [
      {
        selector: 'node[kind = "domain"]',
        style: {
          // make domains visually obvious
          'shape': 'round-rectangle',
          'background-color': 'data(domainColor)',
          'background-opacity': 0.28,    // ↑ ensure domain colours are visible
          'border-color': 'data(domainColor)',
          'border-opacity': 0.65,
          'border-width': 2,
          'padding': '12px',             // space so the label can live inside
          // put the label inside the compound node
          'text-halign': 'left',
          'text-valign': 'top',
          'text-margin-x': 8,
          'text-margin-y': 6,
          'font-size': 14                // tiny bit larger than before
        }
      }
    ];
    return ([...(GRAPH_STYLESHEET as any[]), ...overrides]) as any;
  }, []);

// When the Cytoscape instance is ready, register a click handler on nodes to
// bubble up the selection to the parent component and run the layouts.
const handleCy = useCallback((cy: cytoscape.Core) => {
   if (!cy || (cy as any)._wired) return;
   (cy as any)._wired = true;
    // Hover highlight (node + incident edges)
    cy.on("mouseover", "node", (evt: any) => {
      const n = evt.target;
      n.addClass("hovered");
      n.connectedEdges().addClass("hovered");
    });
    cy.on("mouseout", "node", (evt: any) => {
      const n = evt.target;
      n.removeClass("hovered");
      n.connectedEdges().removeClass("hovered");
    });
    // Edge hover highlight
    cy.on("mouseover", "edge", (evt: any) => {
      const e = evt.target;
      e.addClass("hovered");
    });
    cy.on("mouseout", "edge", (evt: any) => {
      const e = evt.target;
      e.removeClass("hovered");
    });
    // Click to select in parent (nodes)
    cy.on("tap", "node", (evt: any) => {
      const id = evt.target.id();
      const kind = evt.target.data('kind');
      if (id) {
        logEvent('ui.graph.node_select', { id, kind });
      onSelectRef.current?.(id);
      }
    });
    // Click to select transitions (edges)
    cy.on("tap", "edge", (evt: any) => {
      const e = evt.target;
      const id = e?.data?.("id");
      if (id) {
        logEvent('ui.graph.edge_select', { id });
      onSelectRef.current?.(id);
      }
    });

    // ---- Anchor centering + radial ring before main layout ----
    // Compute reduced-motion & layout options inside this scope.
    const prefersReducedMotion =
      typeof window !== "undefined" &&
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const layoutOpts = {
      ...defaultLayout,
      animate: !prefersReducedMotion,
      // Heavier spacing and stiffer edges → fewer overlaps/crossings
      quality: "proof",
      idealEdgeLength: 220,
      nodeRepulsion: 16000,
      edgeElasticity: 0.35,
      gravity: 0.18,
    } as any;

    const runLayouts = () => {
      try {
        // Ensure the canvas knows its real size before laying out
        cy.resize();
        presetRadialPositions(cy);
        // Run PRESET with handler attached *before* run → always unlock the anchor
        const preset = cy.layout({ name: "preset", fit: true, padding: 24 } as any);
        preset.on("layoutstop", () => {
          const a = chooseAnchorNode(cy);
          if (a && a.locked()) a.unlock();
          // Main layout, then resolve overlapping domain compounds and fit
          const l = cy.layout(layoutOpts);
          l.on("layoutstop", () => {
            resolveDomainOverlaps(cy);
            cy.fit(undefined, 32);
          });
          l.run();
        });
        preset.run();
      } catch (e) {
        try { logEvent("ui.graph.preset_layout_error", { error: String(e) }); } catch { /* no-op */ }
      }
    };
    // If nodes haven’t been added yet, wait once for first add.
    if (cy.nodes().length === 0) {
      cy.one("add", runLayouts);
    } else {
      runLayouts();
    }
    // Reflow when the graph container changes size
    const onResize = () => runLayouts();
    window.addEventListener("resize", onResize);
    cy.once("destroy", () => window.removeEventListener("resize", onResize));
}, [defaultLayout]);

  return (
    <div
      className="relative w-full h-[520px] border border-white/10 rounded-md bg-[#0a0f1a]"
      data-oriented={Array.isArray(edges) && edges.some(e => e?.type === "CAUSAL" && (e as OrientedGraphEdge).orientation) ? "1" : "0"}
    >
      <CytoscapeComponent
        elements={elements}
        style={{ width: "100%", height: "100%" }}
        cy={handleCy}
        stylesheet={computedStyles as any}
      />

      {/* Expand button (overlay) */}
      <button
        aria-label="Expand graph"
        className="absolute top-2 right-2 text-xs px-2 py-1 rounded-md bg-white/10 hover:bg-white/20 border border-white/20 backdrop-blur-sm"
        onClick={() => setIsOverlayOpen(true)}
      >
        Expand
      </button>

      {/* Fullscreen overlay with its own Cytoscape instance */}
      {isOverlayOpen && canPortal && createPortal(
        <div
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-[9999] bg-black/70 backdrop-blur-sm"
          onClick={() => setIsOverlayOpen(false)}
        >
          {/* Center the panel within the viewport, slightly smaller than full screen */}
          <div
            className="flex h-full w-full items-center justify-center p-4 sm:p-6"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Fill the padded container → small gap on all sides */}
            <div className="relative w-full h-full rounded-lg border border-white/15 bg-[#0a0f1a] shadow-2xl overflow-hidden">
              {/* OVERLAY LEGEND (same as inline) */}
              <div className="absolute left-4 bottom-4 text-[10px] text-copy/80 bg-black/40 rounded px-2 py-1 pointer-events-none z-10">
                <div className="flex items-center gap-2">
                  <span className="w-3 h-3 rounded-sm inline-block border-2" style={{ borderColor: DECISION_BORDER }} /> Decision
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-3 h-3 rounded-full inline-block border-2" style={{ borderColor: EVENT_BORDER }} /> Event
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-6 inline-block border-t align-middle" style={{ borderColor: EDGE_CAUSAL }} /> Causal
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-6 inline-block border-t align-middle" style={{ borderColor: EDGE_LED_TO }} /> Led to
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-6 inline-block border-t align-middle" style={{ borderColor: EDGE_ALIAS_OF }} /> Alias of
                </div>
                <div className="mt-1">
                  {domainLegend.map((d) => (
                    <div key={d.name} className="flex items-center gap-2">
                      <span className="w-3 h-3 inline-block rounded" style={{ backgroundColor: d.color, opacity: 0.35 }} />
                      {d.name}
                    </div>
                  ))}
                </div>
              </div>
              <button
                aria-label="Close"
                className="absolute top-3 right-3 text-xs px-2 py-1 rounded-md bg-white/10 hover:bg-white/20 border border-white/20 z-10"
                onClick={() => setIsOverlayOpen(false)}
              >
                Close
              </button>
              <CytoscapeComponent
                elements={elements}
                style={{ width: "100%", height: "100%" }}
                cy={handleCy}
                stylesheet={computedStyles as any}
              />
            </div>
          </div>
        </div>,
        document.body
      )}

      <div className="absolute left-2 bottom-2 text-[10px] text-copy/80 bg-black/40 rounded px-2 py-1 pointer-events-none">
        <div className="flex items-center gap-2">
          <span className="w-3 h-3 rounded-sm inline-block border-2" style={{ borderColor: DECISION_BORDER }} /> Decision
        </div>
        <div className="flex items-center gap-2">
          <span className="w-3 h-3 rounded-full inline-block border-2" style={{ borderColor: EVENT_BORDER }} /> Event
        </div>
        <div className="flex items-center gap-2">
          <span className="w-6 inline-block border-t align-middle" style={{ borderColor: EDGE_CAUSAL }} /> Causal
        </div>
        <div className="flex items-center gap-2">
          <span className="w-6 inline-block border-t align-middle" style={{ borderColor: EDGE_LED_TO }} /> Led to
        </div>
        <div className="flex items-center gap-2">
          <span className="w-6 inline-block border-t border-dotted align-middle" style={{ borderColor: EDGE_ALIAS_OF }} /> Alias of
        </div>
        <div className="mt-1">
          {domainLegend.map((d) => (
            <div key={d.name} className="flex items-center gap-2">
              <span className="w-3 h-3 inline-block rounded" style={{ backgroundColor: d.color, opacity: 0.35 }} />
              {d.name}
            </div>
          ))}
        </div>
      </div>
    </div>

  )
}
