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
 * identifier, a snippet/summary fallback, tags, based_on links and an
 * orphan indicator when no links exist. Selection is highlighted with a
 * stronger border and glow.
 */
const EvidenceCard: React.FC<EvidenceCardProps> = ({ item, selected, onSelect, anchorId }) => {
  const orphan = item.orphan ?? (!item.based_on || item.based_on.length === 0);

  // Linked-to-anchor glyph
  const linkedToAnchor = !!(anchorId && Array.isArray(item.led_to) && item.led_to.includes(anchorId));
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
            item.type === "DECISION" ? "bg-vaultred/80" : item.type === "TRANSITION" ? "bg-amber-400/80" : "bg-neonCyan/80"
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
        {item.type === "DECISION"
          ? (item.rationale || item.summary || item.id)
          : item.type === "TRANSITION"
          ? `${item.from ?? ""} → ${item.to ?? ""}`.trim() || (item.reason || item.id)
          : (item.summary || item.snippet || item.id)}
      </div>
      {item.snippet && (
        <div className="text-[12px] text-copy/60 mt-1 line-clamp-1 font-mono">{item.snippet}</div>
      )}
      {item.type === "TRANSITION" && item.reason && (
        <div className="text-[12px] text-copy/60 mt-1 line-clamp-1 font-mono">Reason: {item.reason}</div>
      )}
      {/* Footer: subtle glyph if linked to anchor, based_on / orphan info */}
      <div className="mt-2 text-[10px] font-mono text-copy/60">{ts || ""}</div>
      <div className="mt-1 flex items-center gap-2 text-[11px] text-copy/70">
        {linkedToAnchor && <span title="Linked to anchor">🔗</span>}
        {item.based_on && item.based_on.length > 0 && (
          <span className="truncate">Based on: {item.based_on.join(", ")}</span>
        )}
        {orphan && <span className="italic">Orphan evidence</span>}
      </div>
    </div>
  );
};

export default EvidenceCard;