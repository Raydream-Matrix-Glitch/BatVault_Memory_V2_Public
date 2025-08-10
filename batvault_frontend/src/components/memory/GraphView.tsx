import React, { useEffect } from "react";
import CytoscapeComponent from "react-cytoscapejs";
import cytoscape from "cytoscape";
import coseBilkent from "cytoscape-cose-bilkent";
import type { EvidenceItem } from "../../types/memory";

// Register the cose‑bilkent layout. If the plugin is already registered or
// cannot register (e.g. SSR), ignore the error. Cytoscape will fall back to
// built‑in layouts when necessary.
try {
  cytoscape.use(coseBilkent);
} catch {
  // no-op
}

export interface GraphViewProps {
  /** Array of evidence items to visualise. */
  items: EvidenceItem[];
  /** Id of the currently selected evidence; highlights the corresponding node. */
  selectedId?: string;
  /** Callback invoked when a node is clicked. */
  onSelect: (id: string) => void;
}

/**
 * GraphView renders a Cytoscape graph to display relationships between evidence
 * items. Nodes correspond to evidence items; edges are derived from each
 * item's `based_on` references. If the number of nodes exceeds a threshold
 * (100), the component falls back to a simple list for performance. The
 * selected node is highlighted in neon red.
 */
const GraphView: React.FC<GraphViewProps> = ({ items, selectedId, onSelect }) => {
  // If the graph is too large, degrade gracefully to a list view. This
  // threshold prevents rendering slow graphs and keeps the UI responsive.
  if (items.length > 100) {
    return (
      <div className="p-2 border border-gray-700 rounded-md bg-surface">
        <p className="text-copy text-sm mb-2">
          Graph has {items.length} nodes. Showing simplified list instead.
        </p>
        <ul className="text-copy text-xs space-y-1 max-h-40 overflow-y-auto focus:outline-none">
          {items.map((item) => (
            <li
              key={item.id}
              tabIndex={0}
              onClick={() => onSelect(item.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") onSelect(item.id);
              }}
              className={
                (item.id === selectedId ? "text-vaultred underline" : "hover:text-vaultred") +
                " cursor-pointer"
              }
            >
              {item.id}
            </li>
          ))}
        </ul>
      </div>
    );
  }

  // Build nodes with labels; use a short text based on snippet or id.
  const nodes = items.map((item) => ({
    data: {
      id: item.id,
      label: item.snippet || item.summary || item.id,
    },
    classes: item.id === selectedId ? "selected" : "",
  }));

  // Build edges based on `based_on` relationships. Each parent becomes a source
  // with the current item as target. Edge IDs combine source and target.
  const edges: any[] = [];
  items.forEach((item) => {
    item.based_on?.forEach((parent) => {
      edges.push({
        data: { id: `${parent}-${item.id}`, source: parent, target: item.id },
      });
    });
  });

  // Combine nodes and edges into elements. The `normalizeElements` helper
  // flattens arrays into the format expected by CytoscapeComponent.
  const elements = CytoscapeComponent.normalizeElements({ nodes, edges });

  // Define Cytoscape stylesheet for neon cyberpunk styling.
  const stylesheet = [
    {
      selector: "node",
      style: {
        "background-color": "#00e0e0", // neon cyan for nodes
        label: "data(label)",
        color: "#d1d5db",
        "font-size": 8,
        "text-valign": "center",
        "text-halign": "center",
        "border-width": 1,
        "border-color": "#00e0e0",
      },
    },
    {
      selector: "node.selected",
      style: {
        "background-color": "#ff0030",
        "border-color": "#ff0030",
        "border-width": 2,
        color: "#ffffff",
      },
    },
    {
      selector: "edge",
      style: {
        width: 1,
        "line-color": "#ff0030",
        "target-arrow-color": "#ff0030",
        "target-arrow-shape": "triangle",
        "curve-style": "bezier",
      },
    },
  ];

  // Choose a layout. The cose-bilkent layout provides a good balance between
  // speed and quality. If the plugin is not available, Cytoscape will fall
  // back to a built-in layout automatically.
  const layout = { name: "cose-bilkent", fit: true, padding: 30 };

  // When the Cytoscape instance is ready, register a click handler on nodes to
  // bubble up the selection to the parent component.
  const handleCy = (cy: any) => {
    cy.on("tap", "node", (evt: any) => {
      const id = evt.target.id();
      if (id) onSelect(id);
    });
  };

  return (
    <div className="w-full h-80 border border-gray-700 rounded-md bg-darkbg">
      <CytoscapeComponent
        elements={elements}
        style={{ width: "100%", height: "100%" }}
        cy={handleCy}
        layout={layout}
        stylesheet={stylesheet}
      />
    </div>
  );
};

export default GraphView;