/**
 * Manual fine-tuning for each visual block.
 * • All values are expressed in the ORIGINAL 1024 × 1024 design space.
 * • Positive X  → moves to the RIGHT
 * • Positive Y  → moves DOWN
 * • Scale of 1  → no change
 *
 * Edit, save, refresh – instant result.
 */
export const heroTweaks = {
  branches: {
    dx: 196,
    dy: 74,
    scale: 0.6,
  },
  orb: {
    dx: 13.5,
    dy: 0,
    scale: 1,
  },
  text: {
    dx: 0,
    dy: -170,
    scale: 0.7,
  },
} as const
