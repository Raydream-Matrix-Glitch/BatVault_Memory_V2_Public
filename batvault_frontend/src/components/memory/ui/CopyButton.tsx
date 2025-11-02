import React from "react";

type Props = { text: string; className?: string; label?: string };
export default function CopyButton({ text, className, label = "Copy" }: Props) {
  const onClick = async () => {
    try { await navigator.clipboard.writeText(text); } catch { /* noop */ }
  };
  return (
    <button
      type="button"
      onClick={onClick}
      className={["px-2 py-1 rounded border border-gray-700 text-xs hover:bg-black/30", className].filter(Boolean).join(" ")}
      aria-label="Copy to clipboard"
    >
      {label}
    </button>
  );
}