import React from "react";
import clsx from "clsx";
import type { EvidenceItem } from "../../types/memory";

export interface EvidenceCardProps {
  item: EvidenceItem;
  selected?: boolean;
  onSelect: (id: string) => void;
}

/**
 * Displays a single evidence item with neon cyberpunk styling. Shows the
 * identifier, a snippet/summary fallback, tags, based_on links and an
 * orphan indicator when no links exist. Selection is highlighted with a
 * stronger border and glow.
 */
const EvidenceCard: React.FC<EvidenceCardProps> = ({ item, selected, onSelect }) => {
  // Determine snippet fallback order
  const snippet = item.snippet || item.summary || item.rationale || "No snippet available.";
  const orphan = item.orphan ?? (!item.based_on || item.based_on.length === 0);

  return (
    <div id={`evidence-${item.id}`}
      onClick={() => onSelect(item.id)}
      className={clsx(
        "cursor-pointer mb-3 p-3 rounded-md border transition-colors",
        selected
          ? "border-vaultred shadow-neon-red"
          : "border-vaultred/30 hover:border-vaultred",
        "bg-surface"
      )}
    >
      <div className="font-mono text-xs text-vaultred mb-1 break-all">{item.id}</div>
      {item.timestamp && (
        <div className="text-[10px] font-mono text-neonCyan mb-1">{item.timestamp}</div>
      )}
      <p className="text-sm text-copy mb-2 line-clamp-3">{snippet}</p>
      {/* Tags */}
      {item.tags && item.tags.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-2">
          {item.tags.map((tag) => (
            <span
              key={tag}
              className="text-xs px-2 py-0.5 bg-vaultred/20 text-vaultred rounded-sm"
            >
              {tag}
            </span>
          ))}
        </div>
      )}
      {/* Based_on links */}
      {item.based_on && item.based_on.length > 0 && (
        <div className="text-xs text-neonCyan truncate">
          Based on: {item.based_on.join(", ")}
        </div>
      )}
      {/* Orphan indicator */}
      {orphan && (
        <div className="text-xs text-yellow-400 mt-1">Orphan evidence</div>
      )}
    </div>
  );
};

export default EvidenceCard;