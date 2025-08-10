// src/components/memory/MemoryLayout.tsx
import React from "react";
import VaultLayout from "../../components/origins/VaultLayout";
import { NavMenu } from "../shared/NavMenu";
import { AnimationStepProvider } from "../../components/origins/AnimationStepContext";

export default function MemoryLayout({ children }: { children: React.ReactNode }) {
  const dummyCtx = React.useMemo(
    () => ({ currentStep: "NavMenu", nextStep: () => {} }),
    []
  );

  return (
    <AnimationStepProvider value={dummyCtx}>
      <VaultLayout backgroundVariant="radial" disableFooter>
        {/* optional memory background image here */}
        <NavMenu />
        {children}        {/* render MemoryPage here */}
      </VaultLayout>
    </AnimationStepProvider>
  );
}
