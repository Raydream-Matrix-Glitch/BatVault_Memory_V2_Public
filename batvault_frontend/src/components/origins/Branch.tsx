// src/components/origins/Branch.tsx
import React, { Fragment } from 'react'
import { motion } from 'framer-motion'
import { branchVariants } from './motionVariants'
import { useAnimationController } from './AnimationStepContext'
import { animationSequence } from './animationSequence'

export interface BranchProps {
  d: string
  index: number
  isLast?: boolean
}

/* ------------------------------------------------------------------ */
/*   Technique                                                        */
/*   ─────────                                                        */
/*   1. 10 px butt-capped stroke  → fat base                           */
/*   2.  4 px round-capped stroke → neat tip (gives taper illusion)   */
/*   3. Shared radial-gradient stroke colour                          */
/*   4. Shared cyan outer-glow filter (same as orb)                   */
/* ------------------------------------------------------------------ */
export const Branch: React.FC<BranchProps> = ({ d, index, isLast }) => {
  const { currentStep, nextStep } = useAnimationController()
  const stepIdx = animationSequence.indexOf('Branches')
  const curIdx  = animationSequence.indexOf(currentStep)
  if (curIdx < stepIdx) return null

  return (
    <Fragment>
      {/* render <defs> only once – on the first branch ---------------- */}
      {index === 0 && (
        <defs>
          {/* 
            radial gradient centered at the orb
            — cx/cy are percentages of the SVG viewport;
            — r is how far out it reaches (make sure it covers your longest branch).
            Adjust these three values to match your orb’s position & branch length.
          */}
          <radialGradient
            id="branch-grad"
            gradientUnits="userSpaceOnUse"
            cx="50%"    /* ← tweak to your orb’s X */
            cy="25%"    /* ← tweak to your orb’s Y */
            r="23%"     /* ← tweak so the red reaches your longest tip */
          >
            <stop offset="0%"  stopColor="#00F0FF" />
            <stop offset="100%" stopColor="#FF0033" />
          </radialGradient>

          {/* cyan outer–glow (same palette as orb) --------------------- */}
          <filter id="branch-glow" x="-70%" y="-70%" width="240%" height="240%">
            <feDropShadow
              dx="0"
              dy="0"
              stdDeviation="20"
              floodColor="#00F0FF"
              floodOpacity="0.5"
            />
          </filter>
        </defs>
      )}

      {/* ① thick base layer (10 px) ---------------------------------- */}
      <motion.path
        d={d}
        variants={branchVariants}
        custom={index}
        initial="hidden"
        animate="show"
        stroke="url(#branch-grad)"
        strokeWidth={4}
        strokeLinecap="butt"
        fill="none"
        filter="url(#branch-glow)"
      />

      {/* ② thin highlight on top (4 px) ------------------------------ */}
      <motion.path
        d={d}
        variants={branchVariants}
        custom={index}
        initial="hidden"
        animate="show"
        stroke="url(#branch-grad)"
        strokeWidth={4}
        strokeLinecap="round"
        fill="none"
        /* notify timeline only after the LAST branch’s thin layer ends */
        onAnimationComplete={() =>
          isLast && currentStep === 'Branches' && nextStep()
        }
      />
    </Fragment>
  )
}
