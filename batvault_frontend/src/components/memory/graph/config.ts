// Centralized Cytoscape visual language for Memory graphs.
// Extracted from GraphView.tsx to allow single-point control. :contentReference[oaicite:0]{index=0}

// Shared color tokens (muted palette to match design reference)
export const DECISION_BORDER = "#fca5a5"; // red-300
export const EVENT_BORDER    = "#86efac"; // green-300
export const EDGE_COLOR      = "#94a3b8";
export const EDGE_HOVER      = "#cbd5e1";
export const EDGE_SELECTED   = "#cbd5e1";
export const TEXT_OUTLINE    = "rgba(0,0,0,0.30)";

// Relationship palette
export const EDGE_CAUSAL     = "#93c5fd"; // blue-300
export const EDGE_LED_TO     = "#e5e7eb"; // light gray
export const EDGE_ALIAS_OF   = "#a78bfa"; // violet
export const EDGE_CROSS      = "#fbbf24"; // amber-300

export const GRAPH_STYLESHEET = [
  // Nodes — decision
  {
    selector: "node[kind = 'decision']",
    style: {
      shape: "round-rectangle",
      "background-opacity": 0.08,
      "background-color": DECISION_BORDER,
      "border-width": 2,
      "border-color": DECISION_BORDER,
      label: "data(label)",
      "font-size": 11,
      color: "#ffffff",
      "text-outline-color": TEXT_OUTLINE,
      "text-outline-width": 1,
      "text-valign": "center",
      "text-wrap": "wrap",
      "text-max-width": 240,
      width: "label",
      height: "label",
      padding: 12,
    },
  },
  // Nodes — event
  {
    selector: "node[kind = 'event']",
    style: {
      shape: "ellipse",
      "background-opacity": 0.08,
      "background-color": EVENT_BORDER,
      "border-width": 2,
      "border-color": EVENT_BORDER,
      label: "data(label)",
      "font-size": 11,
      color: "#ffffff",
      "text-outline-color": TEXT_OUTLINE,
      "text-outline-width": 1,
      "text-valign": "center",
      "text-wrap": "wrap",
      "text-max-width": 240,
      width: "label",
      height: "label",
      padding: 12,
    },
  },
  // Edges — base
  {
    selector: "edge",
    style: {
      width: 1.5,
      "line-color": EDGE_COLOR,
      "curve-style": "unbundled-bezier",
      "control-point-step-size": 40,
    },
  },

  // Per-relationship coloring
  { selector: 'edge[label = "CAUSAL"]',
    style: { "line-color": EDGE_CAUSAL, "target-arrow-color": EDGE_CAUSAL, "line-style": "solid" } },
  { selector: 'edge[label = "LED_TO"]',
    style: { "line-color": EDGE_LED_TO, "target-arrow-color": EDGE_LED_TO, "line-style": "solid" } },
  { selector: 'edge[label = "ALIAS_OF"]',
    style: { "line-color": EDGE_ALIAS_OF, "target-arrow-color": EDGE_ALIAS_OF, "line-style": "dotted" } },
  // Cross-domain override (order matters: put this after type rules)
  { selector: 'edge[cross = "true"]',
    style: { "line-color": EDGE_CROSS, "target-arrow-color": EDGE_CROSS, width: 3 } },  

  // Nodes — domain (compound parent)
  {
    selector: "node[kind = 'domain']",
    style: {
      shape: "round-rectangle",
      "background-opacity": 0.12,                 // translucent fill
      "background-color": "data(domainColor)",    // color set per domain in GraphView
      "border-width": 1,
      "border-color": "data(domainColor)",
      label: "data(label)",
      "color": "#ffffff",
      "font-size": 12,
      "text-outline-color": "rgba(0,0,0,0.45)",
      "text-outline-width": 2,
      "text-valign": "top",
      "text-halign": "left",
      "text-margin-x": 8,
      "text-margin-y": 6,
      "padding": 24,
    },
  },

  // Emphasize the anchor
  {
    selector: "node[is_anchor = 'true']",
    style: {
      "background-blacken": -0.2,
      "border-width": 3,
      "text-outline-width": 2,
    },
  },

  // Edges — arrowed types
  {
    selector: "edge[label = 'CAUSAL'], edge[label = 'LED_TO']",
    style: {
      "target-arrow-shape": "triangle",
      "target-arrow-color": EDGE_COLOR,
      width: 1.5,
    },
  },
  // Edges — neutral alias
  {
    selector: "edge[label = 'ALIAS_OF']",
    style: {
      "line-style": "dotted",
    },
  },
  // Hover states
  { selector: "node.hovered", style: { "overlay-color": "#ffffff", "overlay-opacity": 0.08 } },
  {
    selector: "edge.hovered",
    style: { "line-color": EDGE_HOVER, "target-arrow-color": EDGE_HOVER, width: 2 },
  },
  // Selection
  { selector: "node:selected", style: { "border-width": 2, "border-color": "#f87171" } },
  {
    selector: "edge:selected",
    style: { width: 2, "line-color": EDGE_SELECTED, "target-arrow-color": EDGE_SELECTED },
  },
];

export const GRAPH_LAYOUT = {
  name: "cose-bilkent",
  fit: true,
  animate: false,
  padding: 24,
  randomize: false,
  nodeDimensionsIncludeLabels: true,
  // More “spread out” defaults; the instance can still override these.
  quality: "proof",
  idealEdgeLength: 200,
  nodeRepulsion: 12000,
  edgeElasticity: 0.18,
  gravity: 0.22,
  tile: true,
};