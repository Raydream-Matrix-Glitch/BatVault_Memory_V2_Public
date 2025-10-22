import React from "react";
import clsx from "clsx";

type Variant = "muted" | "dangerOutline";
type Size = "xs" | "sm";

export default function Badge({
  children,
  variant = "muted",
  size = "sm",
  className,
}: React.PropsWithChildren<{ variant?: Variant; size?: Size; className?: string }>) {
  const base = "inline-flex items-center rounded-full font-mono";
  const bySize = size === "xs" ? "text-[10px] px-2 py-0.5" : "text-xs px-2.5 py-0.5";
  const byVariant =
    variant === "dangerOutline"
      ? "border border-vaultred/40 bg-vaultred/10 text-vaultred"
      : "border border-white/10 bg-white/5 text-white/80";
  return <span className={clsx(base, bySize, byVariant, className)}>{children}</span>;
}