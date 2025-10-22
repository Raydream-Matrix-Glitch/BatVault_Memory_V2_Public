import React from "react";
import clsx from "clsx";
import type { EvidenceItem } from "../../types/memory";

export interface EvidenceCardProps {
  item: EvidenceItem;
  selected?: boolean;
  onSelect: (id: string) => void;
  anchorId?: string;
}

/**
 * Displays a single evidence item with neon cyberpunk styling. Shows the
 * identifier, a snippet/summary fallback, and tags. Selection is highlighted
 * with a stronger border and glow.
 */
const EvidenceCard: React.FC<EvidenceCardProps> = ({ item, selected, onSelect, anchorId }) => {
  const ts = item.timestamp;
  const tags = item.tags || [];
  return (
    <div
      id={`evidence-${item.id}`}
      onClick={() => onSelect(item.id)}
      className={clsx(
        "cursor-pointer mb-3 p-3 rounded-md border border-white/10 bg-black/20 hover:bg-black/30 transition-colors",
        "shadow-[0_0_30px_rgba(148,163,184,0.08)]",
        selected && "ring-1 ring-vaultred/50"
      )}
    >
      {/* Top row: type chip (EVENT/DECISION) + tag pills */}
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <span className={clsx(
            "w-1.5 h-1.5 rounded-full inline-block",
            item.type === "DECISION" ? "bg-vaultred/80" : "bg-neonCyan/80"
          )} />
          <span className="text-[10px] tracking-wide text-copy/70 uppercase">{item.type || "EVENT"}</span>
        </div>
        <div className="flex flex-wrap gap-1 justify-end">
          {tags.slice(0, 4).map((t) => (
            <span key={t} className="text-[10px] px-1.5 py-0.5 rounded-full border border-white/10 text-neonCyan/90">
              {t}
            </span>
          ))}
        </div>
      </div>
      {/* Body: 2-line title + 1-line muted snippet */}
      <div className="text-sm text-copy line-clamp-2">
        {item.type === "DECISION" ? (item.rationale || item.summary || item.id)
                                  : (item.summary || item.snippet || item.id)}
      </div>
      {item.snippet && (
        <div className="text-[12px] text-copy/60 mt-1 line-clamp-1 font-mono">{item.snippet}</div>
      )}
      {/* Footer: timestamp only (V3 removes based_on/orphan/linked glyphs) */}
      <div className="mt-2 text-[10px] font-mono text-copy/60">{ts || ""}</div>
    </div>
  );
};

export default EvidenceCard;