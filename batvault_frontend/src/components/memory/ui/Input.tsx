import React from "react";
import clsx from "clsx";

export interface InputProps
  extends React.InputHTMLAttributes<HTMLInputElement> {}

const Input: React.FC<InputProps> = ({ className, ...rest }) => {
  return (
    <input
      className={clsx(
        "w-full px-3 py-2 rounded-md bg-surface text-copy border border-gray-700 placeholder-gray-500",
        "focus:outline-none focus:ring-2 focus:ring-vaultred",
        className
      )}
      {...rest}
    />
  );
};

export default Input;