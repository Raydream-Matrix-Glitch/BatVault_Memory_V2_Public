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
      "px-3 py-2 border-b-2 font-medium transition-colors",
      active
        ? "border-vaultred text-vaultred"
        : "border-transparent text-copy hover:text-vaultred hover:border-vaultred",
      className
    )}
  >
    {children}
  </button>
);

export default Tab;