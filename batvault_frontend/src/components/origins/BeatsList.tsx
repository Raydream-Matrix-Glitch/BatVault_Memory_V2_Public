import React from "react";
import { InfoCard } from "./InfoCard";

const BEATS = [
  {
    title: "Marketing",
    body:
      "A campaign lead could sketch “Gen‑Z brand push” and instantly sees content dependencies, CAC forecasts and channel capacity.",
  },
  {
    title: "Manufacturing",
    body:
      "A plant manager could propose “Cut Shanghai night shift,” previewing lead‑time and SLA effects.",
  },
  {
    title: "Finance",
    body:
      "FP&A could model “Float vendor payments by 15 days” and watches cash‑flow, early‑pay discounts and supplier risk light up.",
  },
  {
    title: "Product / Tech",
    body:
      "A PM could draft “Shift to usage‑based pricing,” seeing churn, MRR and roadmap impacts in one view.",
  },
];

export default function BeatsList() {
  return (
    <div className="grid gap-6 md:grid-cols-2">
      {BEATS.map(({ title, body }) => (
        <InfoCard key={title} title={title} body={body} />
      ))}
    </div>
  );
}
