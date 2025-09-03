import React from "react";
import clsx from "clsx";

export interface CardProps {
  className?: string;
  children: React.ReactNode;
}

/** Soft-glow panel with a thin cyan seam, matching Collective’s login panel. */
const Card: React.FC<CardProps> = ({ className, children }) => (
  <div className={clsx("card-neon--cyan p-6 md:p-8 relative", className)}>
    {children}
  </div>
);

export default Card;