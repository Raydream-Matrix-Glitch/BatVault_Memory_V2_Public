// src/pages/ExpiredPage.tsx
import React from "react";
import { useNavigate } from "react-router-dom";
import CollectiveLayout from "./CollectiveLayout";

export default function ExpiredPage() {
  const navigate = useNavigate();

  const handleReturn = () => {
    localStorage.removeItem("access_token"); // Just to be sure
    navigate("/", { replace: true });
  };

  return (
    <CollectiveLayout>
      <div className="flex flex-col items-center justify-center text-white text-center px-6">
        <div className="text-5xl sm:text-6xl md:text-7xl font-bold text-vaultred mb-4 animate-pulse">
          🦇
        </div>
        <h1 className="text-xl sm:text-2xl md:text-3xl font-semibold mb-2">
          You did not move for a while...
        </h1>
        <p className="text-sm sm:text-base md:text-lg text-gray-400 max-w-md mb-6">
          So we logged you out. The bats are watching — and they don’t like idleness.
        </p>
        <button
          onClick={handleReturn}
          className="bg-vaultred hover:bg-red-700 text-white text-sm sm:text-base font-semibold px-6 py-2 rounded-lg transition"
        >
          Return to login
        </button>
      </div>
    </CollectiveLayout>
  );
}
