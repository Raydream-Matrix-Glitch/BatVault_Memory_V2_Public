import React, { useMemo, useEffect } from "react";
import EmptyResult from "./EmptyResult";
import { uiLog } from "../../../apps/web/lib/uiLog";
import { Virtuoso } from "react-virtuoso";
import EvidenceCard from "./EvidenceCard";
import type { EvidenceItem } from "../../types/memory";

export interface EvidenceListProps {
  items: EvidenceItem[];
  errorCode?: string | null; // e.g., "ANCHOR_NOT_FOUND" from backend error envelope
  onSwitchToNatural?: () => void;
  selectedId?: string;
  onSelect: (id: string) => void;
  /** Anchor id for "linked to" glyph logic. */
  anchorId?: string;
  className?: string;
}

/**
 * Virtualized list of evidence cards. Utilises react-virtuoso for performance
 * when dealing with potentially large evidence bundles. Selection is lifted
 * via the onSelect callback.
 */
const EvidenceList: React.FC<EvidenceListProps> = ({
  items,
  selectedId,
  onSelect,
  anchorId,
  className,
  errorCode,
  onSwitchToNatural,
}) => {
  const isEmpty = useMemo(() => !items || items.length === 0, [items]);

  useEffect(() => {
    if (isEmpty || errorCode === "ANCHOR_NOT_FOUND") {
      uiLog("ui.memory.empty_state_shown", {
        reason: errorCode || (isEmpty ? "EMPTY_EVIDENCE" : "UNKNOWN"),
      });
    }
  }, [isEmpty, errorCode]);

  if (isEmpty) {
    return (
      <EmptyResult
        heading={
          errorCode === "ANCHOR_NOT_FOUND"
            ? "Unknown decision reference."
            : "Nothing in the vault under that slug."
        }
        message="Try the Natural path and ask in plain language. We’ll route it for you."
        ctaLabel="Switch to Natural"
        onCta={onSwitchToNatural}
        details="Tip: Ask something like “Why did Panasonic exit plasma TV production?”"
      />
    );
  }

  return (
    <div className={className} style={{ height: "45vh" }}>
      <Virtuoso
        increaseViewportBy={{ top: 160, bottom: 320 }}
        totalCount={items.length}
        itemContent={(index) => {
          const item = items[index];
          return (
            <EvidenceCard
              key={item.id}
              item={item}
              selected={item.id === selectedId}
              onSelect={onSelect}
              anchorId={anchorId}
            />
          );
        }}
      />
    </div>
  );
};
export default EvidenceList;