// src/components/origins/motionVariants.ts
import { Variants } from "framer-motion";


//Sequence Controls

export const preludeVariants: Variants = {
  hidden: {
    opacity: 0,
    clipPath: "inset(0 100% 0 0)",
  },
  typing: {
    opacity: 1,
    clipPath: "inset(0 0% 0 0)",
    transition: {
      duration: 3,
      ease: "linear",
    },
  },
  reverse: {
    opacity: 1,
    clipPath: "inset(0 100% 0 0)",
    transition: {
      duration: 1,
      ease: "easeInOut",
      delay: 0.1,
    },
  },
};

export const orbVariants: Variants = {
  hidden: { opacity: 0, scale: 0.5 },
  show: {
    opacity: 1,
    scale: 1,
    transition: {
      duration: 0.5,
      ease: "easeOut",
    },
  },
  pulse: {
    scale: [1, 1.05, 1],
    transition: {
      duration: 2.5,
      ease: "easeInOut",
      repeat: Infinity,
    },
  },
}

export const branchVariants: Variants = {
  hidden: { pathLength: 0, opacity: 0 },
  show: (i: number) => ({
    pathLength: 1,
    opacity: 1,
    transition: {
      duration: 1 + i * 0.2,
      ease: "easeInOut",
    },
  }),
};

export const logoPrimaryVariants: Variants = {
  hidden: { opacity: 0, y: 20 },
  show: {
    opacity: 1,
    y: 0,
    transition: {
      duration: 0.5,
      ease: "easeOut",
    },
  },
};

export const logoSecondaryVariants: Variants = {
  hidden: { opacity: 0, y: 20 },
  show: {
    opacity: 1,
    y: 0,
    transition: {
      duration: 1,
      ease: "easeOut",
      delay: 0.5,
    },
  },
};

export const navMenuVariants: Variants = {
  hidden: { opacity: 0, y: -10 },
  show: {
    opacity: 1,
    y: 0,
    transition: {
      duration: 0.8,
      ease: "easeOut",
      delay: 0.2,
    },
  },
};


// SCROLL CUE: fades in from below
export const scrollCueVariants: Variants = {
  hidden: { opacity: 0, y: 20 },
  show: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.6, ease: "easeOut", delay: 0.2 },
  },
};


// VARIATION CONTROLS


// ripple (radiation) around the orb

export const rippleVariants: Variants = {
  initial: { scale: 0, opacity: 0 },
  animate: {
    scale: 1.5,
    opacity: 0,
    transition: {
      duration: 5, // adjust to control frequency
      ease: "easeOut",
      repeat: Infinity,
      repeatType: "loop",
    },
  },
};
