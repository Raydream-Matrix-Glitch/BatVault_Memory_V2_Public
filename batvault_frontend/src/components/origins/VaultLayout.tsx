import React, { useLayoutEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { motion } from "framer-motion";
import type { AnimationControls } from "framer-motion";

type VaultLayoutProps = {
  children: React.ReactNode;
  /** “radial” = subtle cyan glow, “plain” = pure #000 background */
  backgroundVariant?: "radial" | "plain";
  disableFooter?: boolean;
  controls?: any;
};

const VaultLayout: React.FC<VaultLayoutProps> = ({
  children,
  backgroundVariant = "radial",
  disableFooter = false,
  controls,
}) => {
  const location = useLocation();
  const mainRef = useRef<HTMLDivElement>(null);

  console.log("🧱 VaultLayout mount", {
    backgroundVariant,
    pathname: location.pathname,
  });

  useLayoutEffect(() => {
    if (mainRef.current) {
      const box = mainRef.current.getBoundingClientRect();
      console.log("📦 VaultLayout main box", {
        top: box.top,
        height: box.height,
        bottom: box.bottom,
        centerY: box.top + box.height / 2,
      });
    }
  }, []);

  return (
    /* Root canvas */
    <div className="relative min-h-screen font-sans text-white overflow-x-hidden bg-background">
      {/* Optional radial glow */}
      {backgroundVariant === "radial" && (
        <motion.div
          className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_center,theme(colors.cyan.400)_0%,transparent_70%)] opacity-0"
          initial={{ opacity: 0 }}
          animate={{ opacity: 0.18 }}
          transition={{ duration: 0.7, ease: "easeInOut" }}
        />
      )}

      {/* Content wrapper */}
      <div className="relative z-10 flex flex-col min-h-screen">
        {/* Top bar / site-logo */}
        <motion.header
          className="p-4"
          variants={{
            hidden: { opacity: 0, y: 0 },
            showText: { opacity: 1, y: 0, transition: { duration: 0.5 } },
          }}
          initial="hidden"
          animate={controls ?? "showText"}
        >
          {/*<img src="/vite.svg" alt="BatVault Logo" className="h-10 w-auto" />*/}
        </motion.header>

        {/* Main slot */}
        <main
          ref={mainRef}
          className="flex-1 flex flex-col items-center justify-center"
        >
          {children}
        </main>

        {/* Optional footer */}
        {!disableFooter && (
          <footer className="py-4 text-center text-xs text-gray-400">
            BatVault Ecosystem · {location.pathname}
          </footer>
        )}
      </div>

      {/* Background radial glow layer */}
      {backgroundVariant === "radial" && (
        <motion.div
          className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_center,theme(colors.cyan.400)_0%,transparent_70%)] opacity-0 z-[-1]"
          initial={{ opacity: 0 }}
          animate={{ opacity: 0.1 }}
          transition={{ duration: 3, ease: "easeInOut" }}
        />
      )}

      
    </div>
  );
};

export default VaultLayout;
