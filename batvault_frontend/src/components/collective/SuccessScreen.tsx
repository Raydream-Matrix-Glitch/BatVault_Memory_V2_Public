// src/pages/SuccessScreen.tsx
import React, { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import CollectiveLayout from "../collective/CollectiveLayout";

export default function SuccessScreen() {
  const navigate = useNavigate();

  useEffect(() => {
    const redirect = () => navigate("collective/vault");

    const timeout = setTimeout(() => {
      if (document.readyState === "complete") {
        redirect();
      } else {
        window.addEventListener("load", redirect, { once: true });
      }
    }, 2000);

    return () => clearTimeout(timeout);
  }, [navigate]);

  return (
    <CollectiveLayout>
      <div className="h-full w-full flex items-center justify-center relative overflow-hidden font-sans">
        <div className="absolute inset-0 bg-gradient-to-br from-vaultred/10 to-transparent blur-3xl opacity-20 animate-pulse-slow" />
        <div className="z-10 flex flex-col items-center text-center animate-fade-in-up">
          <img
            src="/assets/logo/batvault_logo_glow.svg"
            alt="Unlocked"
            className="w-64 h-64 object-contain animate-logo-zoom animate-glow"
          />
          <h1 className="mt-6 text-3xl md:text-4xl font-bold text-vaultred drop-shadow-md">
            Access Granted
          </h1>
          <p className="mt-2 text-sm text-gray-400">
            Redirecting to your vault...
          </p>
        </div>
      </div>
    </CollectiveLayout>
  );
}
