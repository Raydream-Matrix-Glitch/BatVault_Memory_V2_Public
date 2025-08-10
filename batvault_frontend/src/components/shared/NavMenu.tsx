import React, { useEffect } from "react";
import { motion, useAnimation } from "framer-motion";
import { navMenuVariants } from "../origins/motionVariants";
import { useAnimationController } from "../origins/AnimationStepContext";
import originsIcon from "../../assets/origins.svg";
import collectiveIcon from "../../assets/collective.svg";
import memoryIcon from "../../assets/memory.svg";
import { useLocation, Link } from "react-router-dom";

export const NavMenu: React.FC = () => {
  const controls = useAnimation();
  const { currentStep, nextStep } = useAnimationController();
  const location = useLocation();

  /* ── play‑once flag — set in AnimationController after first run ── */
  const STORAGE_KEY = "bv-hero-played";          // ← same key
  const hasPlayedBefore =
    typeof window !== "undefined" &&
    localStorage.getItem(STORAGE_KEY) === "yes";

  /* ── fade‑in logic ──────────────────────────────────────────────── */
  useEffect(() => {
    if (hasPlayedBefore) {
      // First visit already happened → show instantly
      controls.set("show");
      return;
    }

    if (currentStep !== "NavMenu") return;

    console.log("[NAV]   RENDER — currentStep is", currentStep);
    controls.start("show").then(() => {
      console.log("[NAV] Fade complete");
      nextStep(); // advance hero timeline
    });
  }, [currentStep, controls, nextStep, hasPlayedBefore]);

  const menuItems = [
    {
      icon: originsIcon,
      label: "Origins",
      path: "/origins",
      description: "Enter the origin sequence.",
    },
    {
      icon: collectiveIcon,
      label: "Collective",
      path: "/collective",
      description: "Access the shared mind.",
    },
    {
      icon: memoryIcon,
      label: "Memory",
      path: "/memory",
      description: "Recall stored patterns.",
    },
  ];

  return (
    <motion.div
      className="fixed top-4 right-10 z-50 flex items-center space-x-10"
      initial={hasPlayedBefore ? "show" : "hidden"}
      animate={controls}
      variants={navMenuVariants}
    >
      {menuItems.map(({ label, icon, path }) => {
        const isActive = location.pathname.startsWith(path);

        return (
          <Link key={label} to={path}>
            <motion.div
              className="relative flex flex-col items-center cursor-pointer group"
              whileHover={{ scale: 2 }}
              animate={{ scale: isActive ? 1.7 : 1.6 }}
              transition={{ type: "spring", stiffness: 300 }}
            >
              {/* 🔴 Neon red halo glow */}
              <div className="absolute w-20 h-20 rounded-full bg-red-500 opacity-0 group-hover:opacity-40 blur-xl transition duration-300 -z-10" />

              {/* 🧠 Icon */}
              <img
                src={icon}
                alt={label}
                className={`w-12 h-12 object-contain transition duration-300 group-hover:drop-shadow-[0_0_6px_#FF003C] ${
                  label === "Collective" ? "mt-1" : ""
                }`}
              />

              {/* 💬 Tooltip */}
              <div
                className="absolute top-full left-1/2 -translate-x-1/2 mt-1
                           bg-black bg-opacity-80 text-xs text-white px-2 py-1
                           rounded z-10 whitespace-nowrap opacity-0
                           group-hover:opacity-100 transition-opacity duration-300
                           pointer-events-none"
              >
                {label}
              </div>
            </motion.div>
          </Link>
        );
      })}
    </motion.div>
  );
};
