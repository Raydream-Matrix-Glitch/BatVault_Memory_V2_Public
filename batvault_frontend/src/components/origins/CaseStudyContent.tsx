import React from "react";
import { motion } from "framer-motion";

export default function CaseStudyContent() {
  return (
    <motion.section
      className="prose prose-invert max-w-4xl mx-auto p-6 md:p-10"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35 }}
    >
      <p>
        Most organisations leak knowledge: it sits in heads, inboxes, and tools – so teams re-solve the same problems and rely on tribal memory.
      </p>
      <p>
        AI search (RAG) cuts the search tax, but it doesn’t turn insight into accountable action.
      </p>
      <p>
        Tomorrow’s winners retain, surface, and convert knowledge into verifiable decisions that trigger work – systematically and at scale.
      </p>
      <p>
        BatVault makes decisions first-class records: deterministic, LLM-optional, and signed as portable receipts with cross-functional context – so they can be verified, shared, and acted on.
      </p>

      <p className="italic opacity-80 mt-10">More coming soon.</p>
    </motion.section>
  );
}
