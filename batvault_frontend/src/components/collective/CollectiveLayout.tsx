// src/components/collective/CollectiveLayout.tsx
import React from "react";
import VaultLayout from "../origins/VaultLayout";
import { NavMenu } from "../shared/NavMenu";
import { AnimationStepProvider } from "../origins/AnimationStepContext";
import collectiveBg from "../../assets/collectiveBg.svg";

export default function CollectiveLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // reuse the same dummy animation context as MemoryLayout
  const dummyCtx = React.useMemo(
    () => ({ currentStep: "NavMenu", nextStep: () => {} }),
    []
  );

  return (
    <AnimationStepProvider value={dummyCtx}>
      <VaultLayout backgroundVariant="radial" disableFooter>
        <img
          src={collectiveBg}
          alt=""
          className="fixed inset-0 z-0 m-auto w-[80vw] max-w-[2000px]
                     opacity-20 filter
                     drop-shadow-[0_0_20px_rgba(14,232,244,0.6)]
                     transform translate-y-20
                     select-none pointer-events-none"
          />
        <NavMenu />
        {children}
      </VaultLayout>
    </AnimationStepProvider>
  );
}
