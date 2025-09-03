import React, { useEffect, useMemo } from "react";
import CytoscapeComponent from "react-cytoscapejs";
import cytoscape from "cytoscape";
import coseBilkent from "cytoscape-cose-bilkent";
import { logEvent } from "../../utils/logger";
import type { EvidenceItem, EvidenceBundle } from "../../types/memory";

// Register the cose-bilkent layout once.
try { cytoscape.use(coseBilkent); } catch { /* no-op */ }

export interface GraphViewProps {
  /** Evidence items (typically events) */
  items: EvidenceItem[];
  /** Anchor decision and neighbor transitions (optional but recommended) */
  anchor?: EvidenceItem;
  transitions?: EvidenceBundle["transitions"];
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
    .filter('[label = "causal"]')
    .connectedNodes()
    .filter((n) => n.id() !== anchor.id() && n.data('kind') === 'decision')
    .map((n) => n.id()));
  const succeedingDecisionIds = new Set<string>(edgesFromAnchor
    .filter('[label = "causal"]')
    .connectedNodes()
    .filter((n) => n.id() !== anchor.id() && n.data('kind') === 'decision')
    .map((n) => n.id()));

  const leftDecisions  = all.filter((n) => precedingDecisionIds.has(n.id()));
  const rightDecisions = all.filter((n) => succeedingDecisionIds.has(n.id()));
  const others = all
    .not(anchor)
    .not(leftDecisions)
    .not(rightDecisions)
    .toArray();

  // Helper to vertically stack a list at a fixed x with spacing
  const placeColumn = (nodes: cytoscape.NodeSingular[], x: number) => {
    if (nodes.length === 0) return;
    const stepY = Math.min(120, Math.max(80, (h * 0.65) / (nodes.length + 1)));
    const startY = cyy - (stepY * (nodes.length - 1)) / 2;
    nodes.forEach((n, i) => n.position({ x, y: startY + i * stepY }));
  };

  cy.batch(() => {
    // Center the anchor
    anchor.position({ x: cx, y: cyy });

    // Left = preceding decisions, Right = succeeding decisions
    placeColumn(leftDecisions.toArray(),  cx - radius);
    placeColumn(rightDecisions.toArray(), cx + radius);

    // Arrange remaining nodes (mostly events) on a ring
    if (others.length > 0) {
      const step = (2 * Math.PI) / others.length;
      const start = -Math.PI / 2; // start from top
      others.forEach((n, i) => {
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
 * GraphView renders a small relation graph:
 * - Decision node (anchor) in the center (hexagon)
 * - Event nodes around it (ellipse) with edges event -> decision when led_to includes the anchor
 * - Preceding transitions: from -> decision
 * - Succeeding transitions: decision -> to
 */
export default function GraphView({
  items,
  anchor,
  transitions,
  selectedId,
  onSelect,
}: GraphViewProps) {
  const elements = useMemo(() => {
    const els: any[] = [];
    const addNode = (
      id: string,
      rawLabel: string,
      kind: "decision" | "event",
      extra?: Record<string, any>
    ) => {
      if (els.find((e) => e.data && e.data.id === id)) return;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      // Compose a structured, compact label:
      //   DECISION / EVENT
      //   Title / summary
      //   Year (if timestamp exists)
      const year = ((): string | undefined => {
        const ts =
          (items.find(i => i.id === id)?.timestamp) ||
          (anchor?.id === id ? anchor?.timestamp : undefined);
        if (!ts) return undefined;
        try { return String(new Date(ts).getUTCFullYear()); } catch { return undefined; }
      })();
      const kindLabel = kind === "decision" ? "DECISION" : "EVENT";
      const display = [kindLabel, rawLabel, year].filter(Boolean).join("\n");
      els.push({ data: { id, label: display, kind, ...(extra || {}) } });
    };
    // Allow explicit edge IDs so transitions are uniquely selectable.
    const addEdge = (
      src: string,
      dst: string,
      label?: string,
      edgeId?: string
    ) => {
      if (!src || !dst) return;
      const id = edgeId || `${src}->${dst}${label ? `__${label}` : ""}`;
      if (els.find((e) => e.data && e.data.id === id)) return; // de-dupe
      els.push({ data: { id, source: src, target: dst, label } });
    };

    // Decision node
    if (anchor?.id) {
      const label = (anchor as any).title || (anchor as any).option || anchor.id;
      // mark as explicit anchor for deterministic centering
      addNode(anchor.id, String(label), "decision", { is_anchor: true });
    }

    // Events around anchor
    items.forEach((ev) => {
      const label = ev.summary || ev.snippet || ev.id;
      addNode(ev.id, label, "event");
      if (anchor?.id && Array.isArray(ev.led_to) && ev.led_to.includes(anchor.id)) {
        addEdge(ev.id, anchor.id, "supported_by");
      } else if (anchor?.id) {
        addEdge(ev.id, anchor.id);
      }
    });

    // Transitions as decision->decision edges
    const prev = transitions?.preceding ?? [];
    const next = transitions?.succeeding ?? [];
    prev.forEach((t: any) => {
      const from = t?.from; const to = t?.to ?? anchor?.id;
      if (!from || !to) return;
      const fromLabel = (t?.from_title) || String(from);
      addNode(from, String(fromLabel), "decision");
      if (anchor?.id) addNode(anchor.id, anchor.id, "decision");
      addEdge(from, to, "causal", t?.id);
    });
    next.forEach((t: any) => {
      const from = anchor?.id ?? t?.from; const to = t?.to;
      if (!from || !to) return;
      const toLabel = (t?.to_title) || String(to);
      addNode(to, String(toLabel), "decision");
      if (anchor?.id) addNode(anchor.id, anchor.id, "decision");
      addEdge(from, to, "causal", t?.id);
    });

    return els;
  }, [items, anchor?.id, transitions?.preceding, transitions?.succeeding]);

  // Visual language per spec
  const stylesheet = [
    // Nodes
    { selector: "node[kind = 'decision']", style: {
      shape: "round-rectangle",
      "background-opacity": 0,
      "background-color": "transparent",
      "border-width": 2, "border-color": "#ef4444",
      label: "data(label)", "font-size": 11, color: "#ffffff",
      "text-outline-color": "rgba(0,0,0,0.35)", "text-outline-width": 1,
      "text-valign": "center", "text-wrap": "wrap", "text-max-width": 240,
      width: "label", height: "label", padding: 12
    }},
    { selector: "node[kind = 'event']", style: {
      shape: "round-rectangle",
      "background-opacity": 0,
      "background-color": "transparent",
      "border-width": 2, "border-color": "#39FF14",
      label: "data(label)", "font-size": 11, color: "#ffffff",
      "text-outline-color": "rgba(0,0,0,0.35)", "text-outline-width": 1,
      "text-valign": "center", "text-wrap": "wrap", "text-max-width": 240,
      width: "label", height: "label", padding: 12
    }},
    // Edges
    { selector: "edge", style: {
      width: 1.5, "line-color": "#94a3b8", "curve-style": "unbundled-bezier",
      "control-point-step-size": 40, "target-arrow-shape": "triangle",
      "target-arrow-color": "#94a3b8"
    }},
    { selector: "edge[label = 'causal']", style: {
      "line-style": "dashed",
      "line-color": "#f59e0b", "target-arrow-color": "#f59e0b", width: 1.5
    }},
    // Hover halo
    { selector: "node.hovered", style: { "overlay-color": "#ffffff", "overlay-opacity": 0.08 } },
    { selector: "edge.hovered", style: { "line-color": "#e2e8f0", "target-arrow-color": "#e2e8f0", width: 2 } },
    // Selection
    { selector: "node:selected", style: { "border-width": 2, "border-color": "#f87171" } },
    { selector: "edge:selected", style: { width: 2, "line-color": "#e5e7eb", "target-arrow-color": "#e5e7eb" } },
  ];

  const defaultLayout = {
    name: "cose-bilkent",
    fit: true,
    animate: false,
    padding: 24,
    randomize: false,
    nodeDimensionsIncludeLabels: true,
    idealEdgeLength: 160,
    nodeRepulsion: 8000,
    edgeElasticity: 0.2,
    gravity: 0.25,
    tile: true
  };

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
        stylesheet={stylesheet as any}
      />
      <div className="absolute left-2 bottom-2 text-[10px] text-copy/80 bg-black/40 rounded px-2 py-1 pointer-events-none">
        <div className="flex items-center gap-2">
          <span className="w-3 h-3 rounded-sm inline-block border-2" style={{ borderColor: "#ef4444" }} /> Decision
        </div>
        <div className="flex items-center gap-2">
          <span className="w-3 h-3 rounded-sm inline-block border-2" style={{ borderColor: "#39FF14" }} /> Event
        </div>
        <div className="flex items-center gap-2">
          <span className="w-6 inline-block border-t border-dashed align-middle" style={{ borderColor: "#f59e0b" }} /> Transition
        </div>
      </div>
    </div>
  )
}