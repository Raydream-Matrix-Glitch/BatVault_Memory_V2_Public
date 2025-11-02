// src/components/memory/MemoryLayout.tsx
import React from "react";
import VaultLayout from "../../components/origins/VaultLayout";
import { NavMenu } from "../shared/NavMenu";
import { AnimationStepProvider } from "../../components/origins/AnimationStepContext";
import memoryBg from "../../assets/memoryBg.svg";
import "../../styles/neon.css";

export default function MemoryLayout({ children }: { children: React.ReactNode }) {
  // Memory doesn’t use Origins animation steps; provide a stable no-op.
  const dummyCtx = React.useMemo(
    () => ({ currentStep: "NavMenu", nextStep: () => {} }),
    []
  );
  return (
    <AnimationStepProvider value={dummyCtx}>
      <VaultLayout backgroundVariant="radial" disableFooter>
        {/* Shared radial glow + subtle vignette (same universe as Origins/Collective) */}
        <div className="memory-bg memory-bg__radial memory-bg__vignette" />
        {/* Neural tree silhouette (uses assets/memoryBg.svg) */}
        <img
          src={memoryBg}
          alt=""
          className="memory-bg neural-tree select-none"
          aria-hidden="true"
        />
        {/* Global nav */}
        <NavMenu />
        {/* Page content */}
        {children}
      </VaultLayout>
    </AnimationStepProvider>
  );
}
