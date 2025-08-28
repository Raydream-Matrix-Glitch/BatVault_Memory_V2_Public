import React from "react";

export default function VisionStatement() {
  return (
    <section
      className="relative mb-16 p-6 md:p-8 rounded-2xl bg-black/30
                 border border-cyan-500/15 backdrop-blur"
    >
      <h2 className="text-cyan-300 text-2xl font-semibold mb-4">
        BatVault explores the idea of turning every Decision, Event, KPI and
        Transition into a living, queryable map of the organisation
      </h2>
      <p className="italic text-copy">
        One continuously updated graph; many lenses.
      </p>
    </section>
  );
}
