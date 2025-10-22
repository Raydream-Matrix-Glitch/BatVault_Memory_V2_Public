// Centralized Cytoscape visual language for Memory graphs.
// Extracted from GraphView.tsx to allow single-point control. :contentReference[oaicite:0]{index=0}

// Shared color tokens
export const DECISION_BORDER = "#ef4444";
export const EVENT_BORDER    = "#39FF14";
export const EDGE_COLOR      = "#94a3b8";
export const EDGE_HOVER      = "#e2e8f0";
export const EDGE_SELECTED   = "#e5e7eb";
export const TEXT_OUTLINE    = "rgba(0,0,0,0.35)";

export const GRAPH_STYLESHEET = [
  // Nodes — decision
  {
    selector: "node[kind = 'decision']",
    style: {
      shape: "round-rectangle",
      "background-opacity": 0,
      "background-color": "transparent",
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
      "background-opacity": 0,
      "background-color": "transparent",
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
  
  // Nodes — domain (compound parent)
  {
    selector: "node[kind = 'domain']",
    style: {
      shape: "round-rectangle",
      "background-opacity": 0.18,
      "background-color": "data(domainColor)",
      "border-width": 1,
      "border-color": "data(domainColor)",
      "text-valign": "top",
      "text-halign": "left",
      "font-size": 10,
      "text-outline-width": 0,
      "padding": 24,
      label: "data(label)",
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
  idealEdgeLength: 160,
  nodeRepulsion: 8000,
  edgeElasticity: 0.2,
  gravity: 0.25,
  tile: true,
};