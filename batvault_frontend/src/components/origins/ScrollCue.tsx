import React, { useEffect } from 'react';
import { motion, useAnimation } from 'framer-motion';
import { scrollCueVariants } from './motionVariants';
import { useAnimationController } from './AnimationStepContext';
import { animationSequence } from './animationSequence';

interface ScrollCueProps {
  onClick: () => void;
}

export const ScrollCue: React.FC<ScrollCueProps> = ({ onClick }) => {
  const controls = useAnimation();
  const { currentStep, nextStep } = useAnimationController();
  const stepIdx = animationSequence.indexOf('ScrollCue');
  const curIdx  = animationSequence.indexOf(currentStep);

  // true until the animation reaches this cue
  const hidden = curIdx < stepIdx;

  // fire the scroll & advance animation on click
  const onCueClick = () => {
    onClick();
    nextStep();
  };

  useEffect(() => {
    if (!hidden && currentStep === 'ScrollCue') {
      controls.start('show');
    }
  }, [hidden, currentStep, controls]);

  if (hidden) return null;

  return (
    <motion.div
      className="absolute inset-x-0 bottom-8 flex flex-col items-center cursor-pointer"
      variants={scrollCueVariants}
      initial="hidden"
      animate={controls}
      onClick={onCueClick}
    >
      {/* your bouncing arrow & neon text here */}
      <motion.svg
        animate={{ y: [0, -8, 0] }}
        transition={{ duration: 1.2, repeat: Infinity, ease: 'easeInOut' }}
        width={24} height={24} viewBox="0 0 24 24" fill="none"
      >
        <path d="M12 5v14m0 0l-6-6m6 6l6-6" stroke="currentColor" strokeWidth={2} />
      </motion.svg>

      <span
        className="mt-2 font-bold text-lg"
        style={{
          color: '#0ff',
          textShadow:
            '0 0 4px #0ff, 0 0 8px #0ff, 0 0 16px #0ff, 0 0 32px #0ff',
        }}
      >
        Trace the Signal
      </span>
    </motion.div>
  );
};
