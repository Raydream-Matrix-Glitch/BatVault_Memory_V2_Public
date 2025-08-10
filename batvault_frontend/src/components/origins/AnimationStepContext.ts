// src/components/origins/AnimationStepContext.ts
import React, { createContext, useContext } from 'react';
import { animationSequence, AnimationStep } from './animationSequence';

interface AnimationControllerContext {
  currentStep: AnimationStep;
  nextStep: () => void;
}

const AnimationStepContext = createContext<AnimationControllerContext | null>(null);

export function useAnimationController(): AnimationControllerContext {
  const ctx = useContext(AnimationStepContext);
  if (!ctx) {
    throw new Error('useAnimationController must be used within <AnimationStepProvider>');
  }
  return ctx;
}

export const AnimationStepProvider = AnimationStepContext.Provider;
