import React from "react";
import clsx from "clsx";

export interface CardProps {
  className?: string;
  children: React.ReactNode;
}

/**
 * A dark panel with subtle border and neon glows. Use this to group
 * related content on the Memory page.
 */
const Card: React.FC<CardProps> = ({ className, children }) => {
  return (
    <div
      className={clsx(
        // glassy backdrop with subtle red border and neon glow
        "bg-surface border border-vaultred/30 rounded-md p-4 shadow-neon-red backdrop-blur-md",
        className
      )}
    >
      {children}
    </div>
  );
};

export default Card;