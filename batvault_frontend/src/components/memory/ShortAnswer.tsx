import React from "react";
import Button from "./ui/Button";

type AnswerOwner = { name: string; role?: string };
type AnswerBlocks = {
  lead: string;
  description?: string;
  key_events?: string[];
  next?: string;
  owner?: AnswerOwner;
  decision_id?: string;
};
type Props = {
  isStreaming: boolean;
  tokens?: string[] | string;
  blocks?: AnswerBlocks;
  receipts?: string[];
  onCitationClick?: (cid: string) => void;
  onOpenAllowedIds?: () => void;
  onOpenAudit?: () => void;
};

export default function ShortAnswer({
  isStreaming, tokens, blocks,
  receipts = [], onCitationClick, onOpenAllowedIds, onOpenAudit,
}: Props) {
  const streamingText = Array.isArray(tokens) ? tokens.join("") : (tokens ?? "");
  const showStream = isStreaming && streamingText && !blocks?.lead;

  return (
    <div className="mt-6">
      <div className="section-hairline" />
      <div>
        <div className="flex items-center justify-between">
          <h3 className="text-xs tracking-widest uppercase text-neonCyan/80">Short answer</h3>
        </div>
        <div className="mt-4">
          <p className="text-white text-sm leading-snug whitespace-pre-wrap">
            {showStream ? streamingText : (blocks?.lead || "")}
          </p>
          {blocks?.description ? (
            <p className="text-white/90 text-sm leading-snug mt-2">{blocks.description}</p>
          ) : null}
          {blocks?.key_events && blocks.key_events.length > 0 ? (
            <div className="mt-4">
              <div className="text-white text-sm leading-snug font-semibold mb-1">Key events</div>
              <ul className="list-disc pl-6">
                {blocks.key_events.map((evt, idx) => (
                  <li key={idx} className="text-white text-sm leading-snug">{evt}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {blocks?.next ? (
            <p className="text-white text-sm leading-snug mt-4">
              <span className="font-semibold">Next:</span>{" "}{blocks.next}
            </p>
          ) : null}
          {(blocks?.owner || blocks?.decision_id) ? (
            <div className="mt-3 text-white text-sm leading-snug">
              {blocks.owner ? (
                <span>Owner: <span>{blocks.owner.name}</span>{blocks.owner.role ? ` (${blocks.owner.role})` : ""}</span>
              ) : null}
              {blocks.decision_id ? (
                <span className="ml-3">ID: <span>{blocks.decision_id}</span></span>
              ) : null}
            </div>
          ) : null}
        </div>

        {/* Cited IDs (from answer.cited_ids) */}
        {receipts && receipts.length > 0 ? (
          <div className="mt-4">
            <div className="h-0.5" />
            <div className="mt-2 flex gap-2 flex-wrap">
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
          </div>
        ) : null}

        {onOpenAllowedIds || onOpenAudit ? (
          <div className="mt-4 flex items-center justify-end gap-2">
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
        ) : null}
      </div>
    </div>
  );
}