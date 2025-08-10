// src/types.ts

/**
 * Minimal data needed by Scene to render one phase.
 */
export interface SceneEntry {
  stage: "prelude" | "orb" | "branches" | "text";
  scale: number;
}
