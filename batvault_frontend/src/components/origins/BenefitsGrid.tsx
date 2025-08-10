import React from "react";
import { Sparkles, SatelliteDish, SearchCheck } from "lucide-react";
import { InfoCard } from "./InfoCard";

const BENEFITS = [
  {
    icon: <Sparkles />,
    title: "Keeps the why alive",
    body:
      `Ask “Why did we pause the spring campaign?” and get the rationale,
       evidence and ripple effects—no more hunting through chat threads.`,
  },
  {
    icon: <SatelliteDish />,
    title: "Autonomy with coherence",
    body:
      `Teams post updates on their own cadence while leadership sees
       real‑time roll‑ups—edge speed, core alignment.`,
  },
  {
    icon: <SearchCheck />,
    title: "Audit & foresight baked‑in",
    body:
      `Time‑stamps supply traceability, and “look‑ahead” queries reveal
       downstream impacts before budgets or shifts are approved.`,
  },
];

export default function BenefitsGrid() {
  return (
    <div className="grid gap-8 sm:grid-cols-2 lg:grid-cols-3">
      {BENEFITS.map(({ icon, title, body }) => (
        <InfoCard key={title} icon={icon} title={title} body={body} />
      ))}
    </div>
  );
}
