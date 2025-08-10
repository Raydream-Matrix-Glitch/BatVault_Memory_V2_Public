import React from "react";
import { Virtuoso } from "react-virtuoso";
import EvidenceCard from "./EvidenceCard";
import type { EvidenceItem } from "../../types/memory";

export interface EvidenceListProps {
  items: EvidenceItem[];
  selectedId?: string;
  onSelect: (id: string) => void;
  /**
   * Optional className for container styling.
   */
  className?: string;
}

/**
 * Virtualized list of evidence cards. Utilises react-virtuoso for performance
 * when dealing with potentially large evidence bundles. Selection is lifted
 * via the onSelect callback.
 */
const EvidenceList: React.FC<EvidenceListProps> = ({ items, selectedId, onSelect, className }) => {
  return (
    <div className={className} style={{ height: "300px" }}>
      <Virtuoso
        totalCount={items.length}
        itemContent={(index) => {
          const item = items[index];
          return (
            <EvidenceCard
              key={item.id}
              item={item}
              selected={item.id === selectedId}
              onSelect={onSelect}
            />
          );
        }}
      />
    </div>
  );
};

export default EvidenceList;