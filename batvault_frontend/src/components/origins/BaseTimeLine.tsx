import React from "react";

export const BeatsTimeline: React.FC = () => {
  const beats: [string, string][] = [
    ["Marketing", "A campaign lead sketches “Gen‑Z brand push”…"],
    ["Manufacturing", "A plant manager proposes “Cut Shanghai night shift”…"],
    ["Finance", "FP&A models “Float vendor payments by 15 days”…"],
    ["Product / Tech", "A PM drafts “Shift to usage‑based pricing”…"],
  ];

  return (
    <div className="relative pl-8">
      {/* vertical line */}
      <div className="absolute left-3 top-0 bottom-0 w-px bg-cyan-700/30" />
      <ul className="space-y-10">
        {beats.map(([label, copy]) => (
          <li key={label} className="relative">
            {/* node */}
            <span className="absolute -left-[10px] top-1 w-3 h-3 rounded-full bg-cyan-400 ring-2 ring-black" />
            <h5 className="font-semibold">{label} —</h5>
            <p className="text-gray-300">{copy}</p>
          </li>
        ))}
      </ul>
    </div>
  );
};
