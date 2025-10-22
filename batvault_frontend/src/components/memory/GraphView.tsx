import React, { useMemo } from "react";
import CytoscapeComponent from "react-cytoscapejs";
import cytoscape from "cytoscape";
import coseBilkent from "cytoscape-cose-bilkent";
import { logEvent } from "../../utils/logger";
import { GRAPH_STYLESHEET, GRAPH_LAYOUT, DECISION_BORDER, EVENT_BORDER } from "./graph/config";
import type { EvidenceItem, GraphEdge, EnrichedNode } from "../../types/memory";

// Register the cose-bilkent layout once.
try { cytoscape.use(coseBilkent); } catch { /* no-op */ }

// Deterministic domain color from string (HSL -> RGB)
// Ensures different domains get different, stable colors across renders.
function colorForDomain(name: string): string {
  const s = String(name || "");
  let hash = 0;
  for (let i = 0; i < s.length; i++) {
    hash = ((hash << 5) - hash + s.charCodeAt(i)) | 0; // 32-bit hash
  }
  const hue = Math.abs(hash) % 360;
  const sat = 68;
  const light = 50;
  return `hsl(${hue} ${sat}% ${light}%)`;
}

export interface GraphViewProps {
  /** Evidence items (typically events) */
  items: EvidenceItem[];
  /** Anchor decision (optional but recommended) */
  anchor?: EvidenceItem;
  /** Oriented graph edges (V3) */
  edges?: GraphEdge[];
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
  const radius = Math.min(w, h) * 0.38;

  // Partition nodes: decisions that precede/succeed the anchor vs everything else (incl. events)
  const edgesToAnchor   = cy.edges(`[target = "${anchor.id()}"]`);
  const edgesFromAnchor = cy.edges(`[source = "${anchor.id()}"]`);
  const precedingDecisionIds = new Set<string>(edgesToAnchor
    .filter('[label = "CAUSAL"]')
    .connectedNodes()
    .filter((n: cytoscape.NodeSingular): boolean =>
      n.id() !== anchor.id() && n.data('kind') === 'decision')
    .map((n) => n.id()));
  const succeedingDecisionIds = new Set<string>(edgesFromAnchor
    .filter('[label = "CAUSAL"]')
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
  const elements = useMemo(() => {
    const els: any[] = [];
    const nodeIds = new Set<string>();

    // Precompute type hints from edges and items (deterministic sets).
    const eventIds = new Set<string>();
    const decisionIds = new Set<string>();
    // Track domain membership from edges (both endpoints belong to the edge's domain)
    const domainMembers: Record<string, Set<string>> = {};
    const ensureDomain = (name: string) => {
      if (!domainMembers[name]) domainMembers[name] = new Set<string>();
      return domainMembers[name];
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

    const addEdge = (src: string, dst: string, label?: string, edgeId?: string) => {
      if (!src || !dst) return;
      const id = edgeId || `${src}->${dst}${label ? `__${label}` : ""}`;
      if (els.find((e) => e.data && e.data.id === id)) return; // de-dupe
      els.push({ data: { id, source: src, target: dst, label } });
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
      addEdge(e.from, e.to, label, `${e.from}->${e.to}__${label || "EDGE"}`);
      if (e.domain) { ensureDomain(String(e.domain)).add(e.from); ensureDomain(String(e.domain)).add(e.to); }
    });

    // Back-compat: render provided items (events) even if not present in edges.
    (Array.isArray(items) ? items : []).forEach((ev) => { if (ev?.id) addNode(ev.id); });

    // Create compound domain nodes and assign parents
    Object.entries(domainMembers).forEach(([dom, ids]) => {
      const domId = `domain:${dom}`;
      // Add domain parent node
      els.push({ data: { id: domId, label: dom, kind: "domain", domainColor: colorForDomain(dom) } });
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

  // Layout config (centralized)
  const defaultLayout = GRAPH_LAYOUT;

  // When the Cytoscape instance is ready, register a click handler on nodes to
  // bubble up the selection to the parent component and run the layouts.
  const handleCy = (cy: cytoscape.Core) => {
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
        onSelect(id);
      }
    });
    // Click to select transitions (edges)
    cy.on("tap", "edge", (evt: any) => {
      const e = evt.target;
      const id = e?.data?.("id");
      if (id) {
        logEvent('ui.graph.edge_select', { id });
        onSelect(id);
      }
    });

    // ---- Anchor centering + radial ring before main layout ----
    // Compute reduced-motion & layout options inside this scope.
    const prefersReducedMotion =
      typeof window !== "undefined" &&
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const layoutOpts = { ...defaultLayout, animate: !prefersReducedMotion } as any;

    const runLayouts = () => {
      try {
        presetRadialPositions(cy);
        // Commit starting positions so nothing piles up at (0,0)
        cy.layout({ name: "preset", fit: true, padding: 24 } as any).run();
        cy.one("layoutstop", () => {
          const a = chooseAnchorNode(cy);
          if (a && a.locked()) a.unlock();
        });
        cy.layout(layoutOpts).run();
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
};

  return (
    <div className="relative w-full h-[520px] border border-white/10 rounded-md bg-[#0a0f1a]">
      <CytoscapeComponent
        elements={elements}
        style={{ width: "100%", height: "100%" }}
        cy={handleCy}
        stylesheet={GRAPH_STYLESHEET as any}
      />
    
      <div className="absolute left-2 bottom-2 text-[10px] text-copy/80 bg-black/40 rounded px-2 py-1 pointer-events-none">
        <div className="flex items-center gap-2">
          <span className="w-3 h-3 rounded-sm inline-block border-2" style={{ borderColor: DECISION_BORDER }} /> Decision
        </div>
        <div className="flex items-center gap-2">
          <span className="w-3 h-3 rounded-full inline-block border-2" style={{ borderColor: EVENT_BORDER }} /> Event
        </div>
        <div className="flex items-center gap-2">
          <span className="w-6 inline-block border-t align-middle" /> Causal
        </div>
        <div className="flex items-center gap-2">
          <span className="w-6 inline-block border-t align-middle" /> Led to
        </div>
        <div className="flex items-center gap-2">
          <span className="w-6 inline-block border-t border-dotted align-middle" /> Alias of
        </div>
        <div className="flex items-center gap-2">
          <span className="w-3 h-3 inline-block rounded" style={{ backgroundColor: colorForDomain("demo"), opacity: 0.35 }} /> Domain
        </div>
      </div>
    </div>

  )
}
