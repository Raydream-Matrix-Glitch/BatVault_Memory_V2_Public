import React, { useEffect, useState } from "react";
import Input from "./ui/Input";
import Button from "./ui/Button";
import { logEvent } from "../../utils/logger";
import { ROLES, DEFAULT_ROLE, type Role } from "../../utils/roles";

export interface QueryPanelProps {
  /** Called when the user submits a decision reference (anchor) to trace */
  onQueryDecision: (decisionRef: string) => Promise<void> | void;
  /** Indicates whether a request is currently streaming. */
  isStreaming?: boolean;
}

export default function QueryPanel(props: QueryPanelProps) {
  const [decisionRef, setDecisionRef] = useState<string>("");
  const [role, setRole] = useState<Role>(
    ((window as any)?.BV_ACTIVE_ROLE as Role) ?? DEFAULT_ROLE
  );

  // Keep simple demo defaults so the backend accepts the request headers.
  useEffect(() => {
    const w = window as any;
    w.BV_ACTIVE_ROLE = role;
    w.BV_USER_ID = w.BV_USER_ID || "demo-user";
    w.BV_POLICY_VERSION = w.BV_POLICY_VERSION || "v3";
    // Align sensitivity ceiling with role unless overridden
    if (!w.BV_SENSITIVITY_CEILING) {
      const map: Record<string,string> = { analyst: "low", manager: "medium", ceo: "high" };
      w.BV_SENSITIVITY_CEILING = map[String(role).toLowerCase()] || "low";
    }
    // Do NOT force a fake policy key. If ops inject one at runtime, keep it.
    if (!w.BV_POLICY_KEY && (import.meta as any).env?.VITE_POLICY_KEY) {
      w.BV_POLICY_KEY = (import.meta as any).env.VITE_POLICY_KEY;
    }
  }, [role]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const anchor = decisionRef.trim();
    if (!anchor) return;

    try {
      logEvent("ui.memory.trace_clicked", {
        anchor,
        role,
        rid: (window as any).__lastRid ?? null,
      });
    } catch {/* ignore logging errors */}
    await props.onQueryDecision(anchor);   // <-- critical: actually fire the request
  };

  return (
    <div className="space-y-4">
      {/* Role selector */}
      <div className="inline-flex gap-2 rounded-md bg-black/30 backdrop-blur p-1 border border-vaultred/20">
        {(ROLES as Role[]).map((r) => (
          <button
            key={r}
            type="button"
            onClick={() => setRole(r)}
            className={`px-3 py-1 rounded transition ${
              role === r
                ? "bg-vaultred/70 text-white"
                : "bg-black/30 text-copy/80 hover:text-copy"
            }`}
            aria-pressed={role === r}
          >
            {r === "ceo" ? "CEO" : r[0].toUpperCase() + r.slice(1)}
          </button>
        ))}
      </div>

      {/* Single input for anchored query */}
      <form onSubmit={handleSubmit} className="space-y-3">
        <div className="flex items-stretch gap-2">
          <Input
            id="memory-input"
            type="text"
            value={decisionRef}
            onChange={(e) => setDecisionRef(e.target.value)}
            placeholder='Enter an anchor (e.g. "eng#d-eng-010") or paste a title like "Adopt Stripe for EU billing"'
            required
            className="flex-1"
          />
          <Button type="submit" disabled={props.isStreaming}>
            {props.isStreaming ? "Streaming..." : "Trace"}
          </Button>
        </div>
      </form>
    </div>
  );
}
