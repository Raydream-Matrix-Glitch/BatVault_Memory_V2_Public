import React from 'react'
import { motion } from 'framer-motion'
import { orbVariants } from './motionVariants'
import { useAnimationController } from './AnimationStepContext'
import { animationSequence } from './animationSequence'

export const Orb: React.FC = () => {
  const { currentStep, nextStep } = useAnimationController()
  const stepIdx = animationSequence.indexOf('Orb')
  const curIdx = animationSequence.indexOf(currentStep)
  if (curIdx < stepIdx) return null

  return (
    <motion.g
      variants={orbVariants}
      initial="hidden"
      animate="show"
      whileHover="pulse"
      tabIndex={0}
      onAnimationComplete={() => currentStep === 'Orb' && nextStep()}
    >
      <defs>
        {/* 1. Cyan core with fadeout */}
        <radialGradient id="orb-core" cx="50%" cy="50%" r="50%">
          <stop offset="10%" stopColor="#00364d" />
          <stop offset="80%" stopColor="#00F0FF" />
          <stop offset="97%" stopColor="#00F0FF" stopOpacity="0" />
        </radialGradient>

        {/* 2. NEW: Purple-magenta bloom */}
        <radialGradient id="orb-purple-bloom" cx="50%" cy="50%" r="100%">
          <stop offset="0%" stopColor="#D100FF" stopOpacity="0.25" />
          <stop offset="50%" stopColor="#D100FF" stopOpacity="0.15" />
          <stop offset="100%" stopColor="#D100FF" stopOpacity="0" />
        </radialGradient>

        {/* 3. Outer cyan drop shadow */}
        <filter id="blue-glow" x="-100%" y="-100%" width="300%" height="300%">
          <feDropShadow dx="0" dy="0" stdDeviation="30"
                        floodColor="#00F0FF" floodOpacity="4" />
        </filter>
      </defs>

 
      {/* 🔵 Core orb */}
      <circle r={60} fill="url(#orb-core)" />

      {/* 💠 Outer neon halo */}
      <circle r={80} fill="none" filter="url(#blue-glow)" />
    </motion.g>
  )
}
