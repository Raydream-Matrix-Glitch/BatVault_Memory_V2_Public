import React from "react";

interface Props {
  title: string;
  body: string;
  icon?: React.ReactNode;
}

export const InfoCard: React.FC<Props> = ({ title, body, icon }) => (
  <div
    className="group p-4 md:p-6 rounded-2xl bg-black/25 backdrop-blur
               ring-1 ring-cyan-500/10 transition
               hover:-translate-y-1 hover:ring-cyan-400/40
               hover:shadow-cyan-500/10"
  >
    {icon && <div className="text-3xl mb-4 text-cyan-300">{icon}</div>}
    <h4 className="font-semibold mb-2">{title}</h4>
    <p className="text-sm text-copy leading-relaxed">{body}</p>
  </div>
);
