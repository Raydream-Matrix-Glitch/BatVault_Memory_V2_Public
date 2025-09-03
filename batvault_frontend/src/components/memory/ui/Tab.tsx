import React from "react";
import clsx from "clsx";

export interface TabProps {
  active?: boolean;
  onClick: () => void;
  className?: string;
  children: React.ReactNode;
}

/**
 * Simple tab component. When active, the text and border adopt the neon red
 * palette. Otherwise, they remain subdued but brighten on hover.
 */
const Tab: React.FC<TabProps> = ({ active, onClick, className, children }) => (
  <button
    onClick={onClick}
    className={clsx(
      "px-3 py-1 font-medium transition-colors rounded-md",
      active
        ? "bg-black/50 text-vaultred border border-vaultred/50"
        : "text-copy border border-transparent hover:text-vaultred hover:border-vaultred/30 hover:bg-black/40",
      className
    )}
  >
    {children}
  </button>
);

export default Tab;