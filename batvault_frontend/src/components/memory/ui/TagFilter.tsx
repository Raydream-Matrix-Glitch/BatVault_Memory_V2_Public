import React, { useEffect, useMemo, useRef, useState } from "react";
import { uiLog } from "../../../../apps/web/lib/uiLog";

export interface TagFilterProps {
  /** Map of tag -> count. */
  tags: Record<string, number>;
  /** Selected tags (multi-select). */
  selected: string[];
  /** Toggle a tag on/off. */
  onToggle: (tag: string) => void;
  /** Max rows (lines) of chips to show before collapsing. Default: 2. */
  maxRows?: number;
  /** Optional className passthrough. */
  className?: string;
}

/**
 * Chip-based tag filter with:
 * - subtle borders between tags for better separation
 * - max two rows by default with "Show more" / "Show less"
 * - always OR semantics (no AND/OR UI)
 */
const TagFilter: React.FC<TagFilterProps> = ({
  tags,
  selected,
  onToggle,
  maxRows = 2,
  className = ""
}) => {
  const sorted = useMemo(
    () => Object.entries(tags).sort((a,b) => a[0].localeCompare(b[0])),
    [tags]
  );
  const [expanded, setExpanded] = useState(false);
  const wrapRef = useRef<HTMLDivElement|null>(null);
  const [isOverflowing, setOverflowing] = useState(false);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const check = () => {
      setOverflowing(el.scrollHeight - 1 > el.clientHeight);
    };
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, [sorted.length, maxRows]);

  const rowHeight = 24 + 8; // text-xs + py-0.5 ≈ 24px; gap-y-2 = 8px
  const maxHeight = expanded ? "none" : `${rowHeight * Math.max(1, maxRows)}px`;

  return (
    <div className={"flex items-start justify-between gap-3 " + className}>
      {/* Chips */}
      <div ref={wrapRef} className="flex-1 overflow-hidden" style={{ maxHeight }}>
        <div className="flex flex-wrap gap-2">
          {sorted.map(([tag, count]) => {
            const active = selected.includes(tag);
            return (
              <button
                key={tag}
                type="button"
                onClick={() => onToggle(tag)}
                className={
                  "inline-flex items-center gap-1 text-xs font-mono px-2 py-0.5 rounded-full border transition-colors " +
                  (active
                    ? "border-vaultred/60 text-vaultred bg-vaultred/10"
                    : "border-white/10 text-neonCyan hover:text-vaultred hover:border-vaultred/50")
                }
                aria-pressed={active}
              >
                <span className="truncate max-w-[12ch]">{tag}</span>
                <span className="opacity-70">({count})</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* More/Less toggle appears only when overflowing */}
      {isOverflowing && (
        <button
          type="button"
          onClick={() => {
            setExpanded((v) => {
              const next = !v;
              try { uiLog("ui.tag_expand_toggle", { next, maxRows }); } catch { /* no-op */ }
              return next;
            });
          }}
          className="shrink-0 text-xs px-2 py-1 rounded-full border border-white/10 hover:border-vaultred/50 hover:text-vaultred"
          aria-expanded={expanded}
        >
          {expanded ? "Less" : "More"}
        </button>
      )}
    </div>
  );
};

export default TagFilter;