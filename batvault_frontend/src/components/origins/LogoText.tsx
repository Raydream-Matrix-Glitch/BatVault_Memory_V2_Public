// src/components/origins/LogoText.tsx
import React from 'react'
import { motion } from 'framer-motion'
import {
  logoPrimaryVariants,
  logoSecondaryVariants,
} from './motionVariants'
import { useAnimationController } from './AnimationStepContext'
import { animationSequence } from './animationSequence'

export const LogoText: React.FC = () => {
  const { currentStep, nextStep } = useAnimationController()
  const stepIdx = animationSequence.indexOf('LogoText')
  const curIdx = animationSequence.indexOf(currentStep)

  if (curIdx < stepIdx) return null

  return (
    <motion.g
      textAnchor="middle"
      dominantBaseline="middle"
      style={{
        cursor: 'pointer',
        // ensure SVG scales from its own center
        transformBox: 'fill-box',
        transformOrigin: 'center',
      }}
      // a pure "toward‑you" lift is just a slight scale-up
      whileHover={{ scale: 1.05 }}
      transition={{
        type: 'spring',
        stiffness: 600,  // ↑ higher → faster snap
        damping: 70,     // ↑ higher → less bounce
        mass: 0.2        // ↓ lower → accelerates more quickly
      }}
    >
      {/* Primary “BatVault” text */}
      <motion.text
        x={0}
        y={0}
        className="neon-red"
        fontFamily="Montserrat, sans-serif"
        fontWeight="900"
        fontSize={170}
        style={{
          fill: '#ff182b',
          filter: 'drop-shadow(0 0 5px #ff182b) drop-shadow(0 0 5px rgba(255,24,43,0.9))',
        }}
        variants={logoPrimaryVariants}
        initial="hidden"
        animate="show"
      >
        BatVault
      </motion.text>

      {/* Secondary “ORIGINS” text */}
      <motion.text
        x={0}
        y={140}
        className="neon-cyan"
        fontFamily="Montserrat, sans-serif"
        fontWeight="700"
        fontSize={90}
        style={{
          fill: '#00e5ff',
          filter: 'drop-shadow(0 0 2px rgba(0,229,255,1)) drop-shadow(0 0 2px rgba(0,229,255,0.9))',
        }}
        variants={logoSecondaryVariants}
        initial="hidden"
        animate="show"
        onAnimationComplete={() =>
          currentStep === 'LogoText' && nextStep()
        }
      >
        ORIGINS
      </motion.text>
    </motion.g>
  )
}
