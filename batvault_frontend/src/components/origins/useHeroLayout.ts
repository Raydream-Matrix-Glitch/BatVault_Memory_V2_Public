// src/hooks/useHeroLayout.ts
import { createContext, useContext, useEffect, useState } from "react";

/**
 * Information every component needs in order to place itself:
 *  – `scale`   : how much we have to zoom‑in/out so the whole 1024 × 1024 logo fits
 *  – `offsetX` : the absolute centre of the screen on the X axis  (in CSS‑px)
 *  – `offsetY` : the absolute centre of the screen on the Y axis  (in CSS‑px)
 */
export interface HeroLayout {
  scale: number;
  offsetX: number;
  offsetY: number;
}

export const ScaleContext = createContext<HeroLayout>({
  scale: 1,
  offsetX: 0,
  offsetY: 0,
});

/* ------------------------------------------------------------------ */
/* Tunable “magic numbers” — tweak here if you want a different feel. */
/* ------------------------------------------------------------------ */
const DESIGN_SIZE = 1024; // 1024 × 1024 design coordinate system
const MOBILE_MIN_SCALE = 0.9; // orb never shrinks below 75 % on phones
const MOBILE_Y_OFFSET = -120; // upward nudge (px) on tall portrait screens

/**
 * Keeps the logo perfectly centred and uniformly scaled,
 * no matter how the viewport changes.
 */
export default function useHeroLayout(): HeroLayout {
  const [layout, setLayout] = useState<HeroLayout>({
    scale: 1,
    offsetX: 0,
    offsetY: 0,
  });

  useEffect(() => {
    const update = () => {
      const vw = window.innerWidth;
      const vh = window.innerHeight;

      /* --- original desktop‑first scaling ------------------------- */
      let scale = Math.min(vw, vh) / DESIGN_SIZE;

      /* --- mobile guard‑rails ------------------------------------- */
      const tallPortrait = vh / vw >= 1.3; // e.g. 19.5:9 phones
      if (tallPortrait) {
        scale = Math.max(scale, MOBILE_MIN_SCALE);
      }

      /* --- vertical centring tweak -------------------------------- */
      let offsetY = vh * 0.5;
      if (tallPortrait) {
        offsetY += MOBILE_Y_OFFSET; // negative = move upward
      }

      setLayout({
        scale,
        offsetX: vw * 0.5,
        offsetY,
      });
    };

    update(); // run once at start‑up
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  return layout;
}

export const useScale = () => useContext(ScaleContext);
