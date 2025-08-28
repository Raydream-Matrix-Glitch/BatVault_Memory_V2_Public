import React from "react";
import { motion } from "framer-motion";

import VisionStatement from "./VisionStatement";
import BenefitsGrid from "./BenefitsGrid";
import BeatsList from "./BeatsList";
import TodayTomorrowSplit from "./TodayTomorrowSplit";

/* thin cyan fade line */
const Divider = () => (
  <hr
    className="my-14 h-px border-0 bg-gradient-to-r
               from-transparent via-cyan-500/20 to-transparent"
  />
);

export default function CaseStudyContent() {
  return (
    <motion.section
      initial={{ opacity: 0, y: 20 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true }}
      transition={{ duration: 0.6, delay: 0.1 }}
      className="relative mx-auto max-w-5xl overflow-hidden rounded-2xl
                 border border-cyan-500/20 bg-black/30 backdrop-blur
                 px-8  md:px-14 py-20
                 shadow-[0_0_40px_rgba(0,255,255,.10)]
                 prose prose-invert max-w-none
                 prose-headings:text-white prose-h3:mb-3
                 prose-p:text-copy prose-li:marker:text-cyan-400"
    >
      {/* — Headline & intro — */}
      <h1 className="text-3xl md:text-4xl font-bold mb-8 animate-hueShift">
        <span
          className="bg-gradient-to-r from-vaultred via-cyan-400 to-green-400
                     bg-clip-text text-transparent"
        >
          AI is commoditising the how;
        </span>{" "}
        advantage now lives in the what and why
      </h1>

      <p className="mb-14 text-lg leading-relaxed text-copy">
        As automation spreads—from marketing assets to factory rosters—success
        depends on coupling speed with strategic clarity and shared memory.
        Tomorrow’s winners will ask <em>Should we?</em> and <em>Why?</em> as
        reflexively as <em>Can we?</em>
      </p>

      {/* — Vision statement block — */}
      <VisionStatement />

      {/* — Benefits — */}
      <BenefitsGrid />
      <Divider />

      {/* — Four beats — */}
      <h3 className="heading-accent text-2xl font-semibold">
        Four beats, four examples - How BatVault could apply across different domains
      </h3>
      <BeatsList />

      {/* summary */}
      <p className="mt-10 text-copy">
        In short, BatVault is a running experiment in institutional
        wisdom—already usable, and rapidly expanding.
      </p>

      <Divider />

      {/* — Today / tomorrow timeline — */}
      <TodayTomorrowSplit />

      <Divider />

      {/* — Closing line — */}
      <p className="text-lg italic text-copy">
        BatVault’s Vision: turn every decision into a self‑updating knowledge
        graph, so teams can move fast at the edges while the organisation stays
        perfectly aligned at the core.
      </p>
    </motion.section>
  );
}
