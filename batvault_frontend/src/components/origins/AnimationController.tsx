// src/components/origins/AnimationController.tsx
import React, { useCallback, useContext, useEffect, useState } from 'react'
import { PreludeText }  from './PreludeText'
import { Orb }          from './Orb'
import { Branch }       from './Branch'
import { LogoText }     from './LogoText'
import { NavMenu }      from '../shared/NavMenu'

import { AnimationStepProvider } from './AnimationStepContext'
import { animationSequence }     from './animationSequence'
import { ScaleContext }          from './useHeroLayout'
import { heroTweaks }            from './heroTweaks'    // ← NEW ✔
import { ScrollCue }             from './ScrollCue';

/* --------------------------------------------------------------- *
 *  Detect previous play‑through and mark the document <html>.
 * --------------------------------------------------------------- */

const hasPlayedBefore =
  typeof window !== 'undefined' &&
  localStorage.getItem('bv-origins-played') === 'yes'


/* ------------------------------------------------------------------ */
/*                         geometry helpers                           */
/* ------------------------------------------------------------------ */
function computeBranchCenterX (ds: string[]) {
  const xs: number[] = []
  ds.forEach(d => {
    const nums = d.match(/-?\d+(\.\d+)?/g)!.map(Number)
    for (let i = 0; i < nums.length; i += 2) xs.push(nums[i])
  })
  return (Math.min(...xs) + Math.max(...xs)) * 0.5
}

function computeBranchCenterY (ds: string[]) {
  const ys: number[] = []
  ds.forEach(d => {
    const nums = d.match(/-?\d+(\.\d+)?/g)!.map(Number)
    for (let i = 1; i < nums.length; i += 2) ys.push(nums[i])
  })
  return (Math.min(...ys) + Math.max(...ys)) * 0.5
}

/* ---------- original branch path strings – keep yours exactly as-is -------- */
const branchPaths: string[] = [
  "M453.25 262C452.7255 262.053 452.0139 268.8582 451.6484 277.334 450.7863 297.3282 448.3955 306.8408 440.291 322.5 434.091 334.4794 422.3327 348.5025 411 357.4336 401.9327 364.5793 401.1497 365.034 376.5 377.4922 338.0342 396.9332 333.5545 400.3084 298.8594 435.9805L274.5684 460.9551 270.8945 460.3672C267.9856 459.902 266.7799 460.22 265.1094 461.8906 261.6546 465.3454 262.7201 471.777 267 473.3066 269.3298 474.1393 269.9734 474.0829 272.3164 472.8496 275.3902 471.2317 275.967 470.064 275.9844 465.4336 275.9994 461.4566 276.6054 460.7567 303.75 433.3242 325.9016 410.9376 333.4032 404.005 340.9316 398.9609 351.1221 392.1333 399 367.3447 399 368.8965 399 369.4158 396.7993 373.3896 394.1094 377.7266 386.6341 389.7792 384.4579 395.9056 378.918 420.5 377.803 425.45 376.0089 433.325 374.9297 438 373.8505 442.675 372.5312 448.525 371.9981 451 371.465 453.475 370.7876 456.5138 370.4941 457.752 370.1771 459.0897 368.4474 460.7159 366.2305 461.7598 362.8449 463.3539 362.5 463.8833 362.5 467.4844 362.5 470.8841 362.8821 471.5873 365.1719 472.3926 370.6792 474.3294 374.9325 468.4775 372 462.9981 371.0465 461.2164 372.3673 453.1569 375.0313 444.5 375.539 442.85 377.2996 435.2 378.9453 427.5 387.6393 386.8216 391.7662 378.8455 416.9668 354 432.4784 338.707 438.5253 330.6653 444.7344 317.0742 449.3675 306.9328 451.6513 297.551 452.4219 285.5 452.7736 280 453.319 272.4625 453.6328 268.75 453.9692 264.7705 453.8137 262 453.2559 262 453.2539 262 453.2519 261.9998 453.2499 262Z",
  "M487.584 261.5 487.041 352.5C486.743 402.55 486.2013 443.957 485.8359 444.5156 485.4706 445.0743 484.6923 448.9823 484.1055 453.2012 482.88 462.0117 479.2842 473.0291 474.2695 483.3398 472.354 487.2783 468.5277 495.1296 465.7656 500.7871 463.0035 506.4446 459.552 515.1818 458.0957 520.2051 455.6223 528.7365 455.2317 529.4118 452.1738 530.4414 450.3733 531.0476 448.4789 532.2085 447.9648 533.0215 446.4844 535.3627 446.8468 540.1746 448.6543 542.1719 451.0956 544.8695 457.4403 544.6372 459.4434 541.7773 461.5478 538.7728 461.4011 535.7791 459 532.7266 457.9 531.3281 457 529.5519 457 528.7793 457 525.1215 463.6923 508.3248 471.3613 492.7363 479.6794 475.8285 484.272 463.1706 485.4981 453.7734 486.11 449.0834 487.9241 446.2205 488.084 449.6914 488.5207 459.1722 495.588 479.7797 502.9375 493 512.4014 510.0236 518.9955 528.5206 517.0898 532.7031 514.3565 538.7021 516.9314 544 522.582 544 524.9262 544 526.4431 543.2527 527.9277 541.3652 531.9025 536.3121 529.8842 531.1536 523.5273 530.1191 519.119 529.4017 519.1942 529.5199 515.1348 516.9063 513.4367 511.6299 508.4363 500.1903 504.0234 491.4844 499.6106 482.7784 496 474.9225 496 474.0273 496 473.1322 495.5957 471.9717 495.1016 471.4492 493.541 469.7992 490.0712 455.4441 489.0234 446.2988 488.4383 441.1918 487.9263 400.5825 487.8008 349.5Z",
  "M520.2891 261.5 520.1445 273.5742C519.9402 290.6238 522.719 305.4457 528.2012 316.5566 535.4679 331.2845 548.7831 346.8853 566.8926 361.8867 577.1631 370.3946 588.3241 388.3363 592.7793 403.5 593.9104 407.35 596.8802 418.6 599.3789 428.5 601.8776 438.4 604.9901 449.7224 606.2949 453.6602 608.3317 459.8068 608.4836 461.1508 607.3691 463.1602 605.5237 466.4873 605.7019 468.7019 608 471 612.048 475.048 617 472.8136 617 466.9395 617 463.3002 616.7339 462.8968 613.9785 462.3457 612.3171 462.0134 610.6339 461.2375 610.2363 460.6211 608.8393 458.4546 605.8771 448.2978 600.543 427.3731 597.5807 415.7527 594.2337 403.1527 593.1035 399.3731 590.9449 392.1538 583.9748 379.0612 578.9609 372.8086 577.3331 370.7785 576.3634 368.8931 576.8066 368.6191 577.9454 367.9153 624.1572 391.6844 632 397.0078 643.3143 404.6876 648.8652 409.7591 675.7324 436.9512 700.0746 461.5877 701.9663 463.7566 701.9824 467.0508 702.0032 471.3112 703.4651 473 707.1289 473 710.4824 473 713 470.6414 713 467.5 713 464.0962 710.4 462 706.1797 462 702.6907 462 701.1016 460.5798 675.0019 434.1484 659.8758 418.8301 644.125 403.8285 640 400.8105 630.4498 393.8233 615.6582 385.3886 596 375.7207 575.0513 365.4182 566.1069 359.7036 555.7461 350.0059 538.5648 333.924 526.625 314.4401 523.5684 297.5 522.6752 292.55 521.5721 282.425 521.1172 275Z"
 ]

/* ---------- key anchor points (in **original 1024×1024** space) ----------- */
const ORB_CX = 500.5066
const ORB_CY = 178.7849

const BRANCH_CX = computeBranchCenterX(branchPaths)
const BRANCH_CY = computeBranchCenterY(branchPaths)

/*  base deltas – before any manual tweak ----------------------------------- */
const BASE_DX_ORB  = BRANCH_CX - ORB_CX
const BASE_DY_ORB  = ORB_CY    - BRANCH_CY
const BASE_DY_TEXT = 680       - BRANCH_CY   // “BatVault / ORIGINS” baseline

/* ------------------------------------------------------------------ */
/*                               component                            */
/* ------------------------------------------------------------------ */
const VIEWBOX = 1024
const HALF    = VIEWBOX / 2

export const AnimationController: React.FC<{ onScrollCue: () => void }> = ({ onScrollCue }) => {  // ② accept prop
  const [stepIdx, setStepIdx] = useState(0)
  const currentStep = animationSequence[stepIdx]
  const nextStep    = useCallback(
    () => setStepIdx(i => Math.min(i + 1, animationSequence.length - 1)),
    [],
  )

  const { scale: responsiveScale } = useContext(ScaleContext)


  /* ───────────────── play‑once logic ───────────────── */
  const STORAGE_KEY = "bv-hero-played";

  /* 1️⃣  on mount: skip to final step if visitor has already seen the animation */
  useEffect(() => {
    if (window.localStorage.getItem(STORAGE_KEY) === "yes") {
      setStepIdx(animationSequence.length - 1);
    }
  }, []);



  /* 2️⃣  whenever we arrive at the final step for the first time, mark as played */
  useEffect(() => {
    if (stepIdx === animationSequence.length - 1) {
      window.localStorage.setItem(STORAGE_KEY, "yes");
    }
  }, [stepIdx]);

  /* -------------------------------------------------------------------- */

  
  return (
    <AnimationStepProvider value={{ currentStep, nextStep }}>
      <PreludeText />

      <svg
        width="100%"
        height="100%"
        viewBox={`0 0 ${VIEWBOX} ${VIEWBOX}`}
        preserveAspectRatio="xMidYMid meet"
      >
        <g transform={`translate(${HALF} ${HALF}) scale(${responsiveScale})`}>

          {/* ---------- 1)  BRANCHES  ------------------------------------ */}
          <g
            transform={`
              translate(${ -BRANCH_CX + heroTweaks.branches.dx }
                        ${ -BRANCH_CY + heroTweaks.branches.dy })
              scale(${ heroTweaks.branches.scale })
            `}
          >
            {branchPaths.map((d, i) => (
              <Branch
                key={i}
                d={d}
                index={i}
                isLast={i === branchPaths.length - 1}
              />
            ))}
          </g>

          {/* ---------- 2)  ORB  ---------------------------------------- */}
          <g
            transform={`
              translate(${ BASE_DX_ORB + heroTweaks.orb.dx }
                        ${ BASE_DY_ORB + heroTweaks.orb.dy })
              scale(${ heroTweaks.orb.scale })
            `}
          >
            <Orb />
          </g>

          {/* ---------- 3)  LOGO TEXT  ---------------------------------- */}
          <g
            transform={`
              translate(${          heroTweaks.text.dx }
                        ${ BASE_DY_TEXT + heroTweaks.text.dy })
              scale(${ heroTweaks.text.scale })
            `}
          >
            <LogoText />
          </g>
        </g>
      </svg>

      <div className="hero-nav-wrapper">
        <NavMenu />
      </div>

    <ScrollCue onClick={onScrollCue} />

    </AnimationStepProvider>
  )
}

export default AnimationController
