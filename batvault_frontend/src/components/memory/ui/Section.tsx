import React from "react";

export default function Section({
  title,
  count,
  right,
}: {
  title: string;
  count?: number | string;
  right?: React.ReactNode;
}) {
  return (
    <div>
      <div className="section-hairline" />
      <div className="flex items-center justify-between">
        <h3 className="text-xs tracking-widest uppercase text-neonCyan/80">
          {title}
          {typeof count !== "undefined" && (
            <span className="ml-2 text-[10px] opacity-60">({count})</span>
          )}
        </h3>
        {right}
      </div>
    </div>
  );
}