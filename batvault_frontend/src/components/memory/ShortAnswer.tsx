import React from "react";
import Button from "./ui/Button";

type Props = {
  isStreaming: boolean;
  tokens?: string[] | string;
  mainAnswer?: string;
  leadBeforeEvents?: string;
  eventsFromShort?: string[];
  anchorHeading?: string;
  anchorDescription?: string;
  nextFromShort?: string;
  nextTitle?: string;
  receipts?: string[];
  onCitationClick?: (cid: string) => void;
  onOpenAllowedIds?: () => void;
  onOpenAudit?: () => void;
};

export default function ShortAnswer({
  isStreaming, tokens, mainAnswer, leadBeforeEvents, eventsFromShort,
  anchorHeading, anchorDescription, nextFromShort, nextTitle,
  receipts = [], onCitationClick, onOpenAllowedIds, onOpenAudit,
}: Props) {
  const streamingText = Array.isArray(tokens) ? tokens.join("") : (tokens ?? "");
  const bodyText = mainAnswer || (isStreaming ? String(streamingText) : "");

  return (
    <div className="mt-6">
      <div className="section-hairline" />
      <div>
        <div className="flex items-center justify-between">
          <h3 className="text-xs tracking-widest uppercase text-neonCyan/80">Short answer</h3>
        </div>
        <div className="mt-4">
          {(!isStreaming && eventsFromShort && eventsFromShort.length > 0) ? (
            <>
              {anchorHeading ? (
                <div className="text-white leading-snug">
                  <div className="text-base font-semibold mb-2">{anchorHeading}</div>
                  <div className="text-sm">{anchorDescription}</div>
                </div>
              ) : (
                <p className="text-white text-sm leading-snug">{leadBeforeEvents}</p>
              )}
              <div className="mt-4">
                <div className="text-white text-sm leading-snug font-semibold mb-1">Key Events:</div>
                <ul className="list-disc pl-6">
                  {eventsFromShort!.map((evt, idx) => (
                    <li key={idx} className="text-white text-sm leading-snug">{evt}</li>
                  ))}
                </ul>
              </div>
            </>
          ) : (
            <p className="text-white text-sm leading-snug">{bodyText}</p>
          )}

          {(nextFromShort || nextTitle) && (
            <>
            {/* SAME STYLE AS THE LEAD TEXT */}
            <p className="text-white text-sm leading-snug mt-4">
              <span className="font-semibold">Next:</span>{" "}{nextFromShort ?? nextTitle}
            </p>
            </>
          )}
        </div>

        {/* badges + actions row */}
        {(receipts && receipts.length > 0) || onOpenAllowedIds || onOpenAudit ? (
          <div className="mt-4 flex items-center justify-between gap-3 flex-wrap">
            {/* receipts on the left */}
            <div className="flex-1 min-w-0">
              {receipts && receipts.length > 0 && (
                <div className="flex gap-2 overflow-x-auto whitespace-nowrap no-scrollbar">
                  {receipts.map((cid) => (
                    <button
                      key={cid}
                      type="button"
                      onClick={() => onCitationClick && onCitationClick(cid)}
                      className="text-xs px-2 py-0.5 rounded-full border border-vaultred/50 text-vaultred hover:bg-vaultred/30 transition-colors"
                    >
                      <span className="font-mono">{cid}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            {/* actions on the right */}
            <div className="flex items-center gap-2 shrink-0">
              {onOpenAllowedIds && (
                <Button type="button" onClick={onOpenAllowedIds} variant="secondary" className="text-xs">
                  Expand
                </Button>
              )}
              {onOpenAudit && (
                <Button type="button" onClick={onOpenAudit} className="text-xs">
                  Audit
                </Button>
              )}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}