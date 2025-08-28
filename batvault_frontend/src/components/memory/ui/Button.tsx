import React from "react";
import clsx from "clsx";

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /**
   * Visual variant of the button. Primary uses the neon red palette,
   * secondary is a more subdued surface style.
   */
  variant?: "primary" | "secondary";
}

const Button: React.FC<ButtonProps> = ({
  variant = "primary",
  className,
  children,
  ...rest
}) => {
  const base =
    "px-4 py-2 rounded-md font-medium transition-colors focus:outline-none";
  const variants: Record<string, string> = {
    primary:
      "bg-vaultred text-white hover:bg-red-600 shadow-neon-red focus:ring-2 focus:ring-vaultred",
    secondary:
      "bg-surface text-copy border border-vaultred/50 hover:bg-darkbg shadow-neon-red",
  };
  return (
    <button
      className={clsx(base, variants[variant], className)}
      {...rest}
    >
      {children}
    </button>
  );
};

export default Button;