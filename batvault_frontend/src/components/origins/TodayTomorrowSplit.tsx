import React from "react";

export default function TodayTomorrowSplit() {
  const today = [
    "Boot in minutes on Micro‑K8s or Docker Compose.",
    "Capture → index → trace pipeline with full, click‑through provenance—ideal for audit, training and observability demos.",
  ];

  const tomorrow = [
    "Self‑updating knowledge graph fed by Jira, Git, ERP, CRM, plant logs and ad platforms.",
    "Intent‑aware query pipeline that picks the best retrieval mix and runs “what‑if” simulations before commitments are made.",
    "Pluggable integrations for SSO, RBAC and existing BI tools, so insights surface where teams already work.",
    "Managed or on‑prem deployment with SLA‑backed hosting and role‑based dashboards for every function.",
  ];

  return (
    <div
      className="md:grid md:grid-cols-2 md:gap-12
                 space-y-10 md:space-y-0"
    >
      {/* — TODAY — */}
      <div
        className="relative p-8 rounded-2xl bg-black/25 backdrop-blur
                   ring-1 ring-cyan-500/15
                   before:absolute before:inset-0 before:border-l
                   before:border-cyan-500/30"
      >
        <h3 className="text-white font-semibold mb-5">
          BatVault today — what the PoC already proves
        </h3>
        <ul className="list-disc pl-6 space-y-3">
          {today.map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
      </div>

      {/* — TOMORROW — */}
      <div
        className="p-8 rounded-2xl bg-black/25 backdrop-blur
                   ring-1 ring-cyan-500/15"
      >
        <h3 className="text-white font-semibold mb-5">
          BatVault tomorrow — where the roadmap points
        </h3>
        <ul className="list-disc pl-6 space-y-3">
          {tomorrow.map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}
