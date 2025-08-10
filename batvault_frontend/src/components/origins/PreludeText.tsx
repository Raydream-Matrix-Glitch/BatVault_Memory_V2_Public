// src/components/origins/PreludeText.tsx
import React, { useContext, useEffect } from 'react';
import { motion, useAnimation } from 'framer-motion';
import { preludeVariants } from './motionVariants';
import { useAnimationController } from './AnimationStepContext';
import { ScaleContext } from "./useHeroLayout";

export const PreludeText: React.FC = () => {
  const controls = useAnimation();
 const { currentStep, nextStep } = useAnimationController();
  const { scale, offsetX, offsetY } = useContext(ScaleContext);

  useEffect(() => {
    if (currentStep !== 'PreludeText') return;

    console.log('[PRELUDE] Animation started');
    // Start typing
    controls
      .start('typing')
      .then(() => {
        console.log('[PRELUDE] Typing complete');
        // Reverse and disappear
        return controls.start('reverse');
      })
      .then(() => {
        console.log('[PRELUDE] Reverse complete');
        nextStep();
      });
  }, [currentStep, controls, nextStep]);

  if (currentStep !== 'PreludeText') {
    return null;
  }

   return (
   <motion.div
     className="absolute inset-0 flex items-center justify-center text-white font-mono text-2xl"
     variants={preludeVariants}
     initial="hidden"
     animate={controls}
     style={{
      transform: `scale(${scale}) translateY(-30px)`, // ← adjust this value as needed
    }}
   >
     <span>A Signal Emerges…</span>
   </motion.div>
 );
 };