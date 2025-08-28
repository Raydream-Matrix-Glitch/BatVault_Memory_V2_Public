// src/components/origins/animationSequence.ts

/**
 * Defines the ordered steps of the Origins animation.
 * Components read their position in this array via useAnimationStep.
 */
export type AnimationStep =
  | 'PreludeText'
  | 'Orb'
  | 'Branches'
  | 'LogoText'
  | 'NavMenu'
  | 'ScrollCue';

export const animationSequence: AnimationStep[] = [
  'PreludeText',
  'Orb',
  'Branches',
  'LogoText',
  'NavMenu',
  'ScrollCue',
];
