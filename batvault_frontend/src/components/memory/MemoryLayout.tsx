// src/components/memory/MemoryLayout.tsx
import React from "react";
import VaultLayout from "../../components/origins/VaultLayout";
import { NavMenu } from "../shared/NavMenu";
import { AnimationStepProvider } from "../../components/origins/AnimationStepContext";
import memoryBg from "../../assets/memoryBg.svg";

export default function MemoryLayout({ children }: { children: React.ReactNode }) {
  const dummyCtx = React.useMemo(
    () => ({ currentStep: "NavMenu", nextStep: () => {} }),
    []
  );

  return (
    <AnimationStepProvider value={dummyCtx}>
      <VaultLayout backgroundVariant="radial" disableFooter>
        {/* memory background art */}
        <img
          src={memoryBg}
          alt=""
          className="fixed inset-0 z-0 m-auto w-[80vw] max-w-[2000px] opacity-20 drop-shadow-[0_0_20px_rgba(14,232,244,0.6)] translate-y-20 select-none pointer-events-none"
        />
        <NavMenu />
        {children} {/* render MemoryPage here */}
      </VaultLayout>
    </AnimationStepProvider>
  );
}
