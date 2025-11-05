// src/pages/origins/OriginsHero.tsx

import { useRef } from "react";
import AnimationController from "./AnimationController";
import VaultLayout from "./VaultLayout";
import useHeroLayout, { ScaleContext } from "./useHeroLayout";
import CaseStudyContent from "./CaseStudyContent";  
import { logEvent } from "../../utils/logger";


export default function OriginsHero() {
  // compute the responsive scale for the logo & nav
  const responsiveScale = useHeroLayout();

  logEvent("ui.origins_render", { responsiveScale });

    // ① create a ref for the next page anchor
  const nextPageRef = useRef<HTMLDivElement>(null);

  // ② build the click handler that scrolls into view
  const handleScrollCue = () => {
    nextPageRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  return (
    <ScaleContext.Provider value={responsiveScale}>
      <VaultLayout>
        <div style={{ position: "relative", width: "100vw", height: "100vh", overflow: "visible" }}>
          {/* pass our scroll handler into the controller */}
          <AnimationController onScrollCue={handleScrollCue} />
        </div>

        {/* ③ the “next page” section to scroll to */}
<div ref={nextPageRef} className="min-h-screen">
  <CaseStudyContent />
</div>
      </VaultLayout>
    </ScaleContext.Provider>
  );
}
