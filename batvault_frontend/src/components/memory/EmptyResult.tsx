import React from "react";

type Props = {
  heading?: string;
  message?: string;
  ctaLabel?: string;
  onCta?: () => void;
  details?: string;
};

const EmptyResult: React.FC<Props> = ({
  heading = "Nothing in the vault under that slug.",
  message = "Try the Natural path and ask in plain language. We’ll route it for you.",
  ctaLabel = "Switch to Natural",
  onCta,
  details,
}) => {
  return (
    <div
      className="mt-6 rounded-xl border border-cyan-500/30 bg-white/0 p-6 text-cyan-100 shadow-[0_0_0_1px_rgba(0,255,255,0.06),0_0_24px_rgba(0,255,255,0.12)]"
      role="status"
      aria-live="polite"
    >
      <div className="flex items-start gap-3">
        <div className="mt-0.5 h-2 w-2 shrink-0 rounded-full bg-cyan-400 shadow-[0_0_12px_rgba(34,211,238,0.9)]" />
        <div className="space-y-2">
          <h3 className="text-lg font-semibold">{heading}</h3>
          <p className="text-sm text-cyan-200/80">{message}</p>
          {details ? (
            <p className="text-xs text-cyan-200/60">{details}</p>
          ) : null}
          {onCta ? (
            <div className="pt-2">
              <button
                type="button"
                onClick={onCta}
                className="rounded-lg border border-cyan-500/60 px-3 py-1.5 text-sm font-medium text-cyan-100 hover:bg-cyan-500/10 focus:outline-none focus:ring-2 focus:ring-cyan-400/50"
              >
                {ctaLabel}
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
};

export default EmptyResult;