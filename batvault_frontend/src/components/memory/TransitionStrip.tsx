import React from "react";
import type { EvidenceBundle } from "../../types/memory";

export interface TransitionStripProps {
  transitions?: EvidenceBundle["transitions"];
  onSelect?: (id: string) => void;
  className?: string;
}

function Pill({ label, onClick }: { label: string; onClick?: () => void }) {
  return (
    <button
      onClick={onClick}
      className="text-[11px] px-2 py-1 rounded-full border border-white/10 bg-black/30 hover:bg-black/40 transition mr-1 mb-1"
      title={label}
    >
      {label}
    </button>
  );
}

/**
 * Compact row of preceding/succeeding decisions derived from transitions.
 * Uses `from_title`/`to_title` when present; falls back to IDs.
 */
export default function TransitionStrip({ transitions, onSelect, className }: TransitionStripProps) {
  const preceding = transitions?.preceding ?? [];
  const succeeding = transitions?.succeeding ?? [];

  if (preceding.length === 0 && succeeding.length === 0) return null;

  return (
    <div className={className}>
      {preceding.length > 0 && (
        <div className="mb-2">
          <h4 className="text-[10px] tracking-widest uppercase text-copy/70 mb-1">Came from</h4>
          <div className="flex flex-wrap">
            {preceding.map((t: any) => {
              const id = t?.from;
              const label = t?.from_title || t?.from || "";
              if (!id) return null;
              return <Pill key={id} label={label} onClick={() => onSelect?.(id)} />;
            })}
          </div>
        </div>
      )}
      {succeeding.length > 0 && (
        <div>
          <h4 className="text-[10px] tracking-widest uppercase text-copy/70 mb-1">Next</h4>
          <div className="flex flex-wrap">
            {succeeding.map((t: any) => {
              const id = t?.to;
              const label = t?.to_title || t?.to || "";
              if (!id) return null;
              return <Pill key={id} label={label} onClick={() => onSelect?.(id)} />;
            })}
          </div>
        </div>
      )}
    </div>
  );
}