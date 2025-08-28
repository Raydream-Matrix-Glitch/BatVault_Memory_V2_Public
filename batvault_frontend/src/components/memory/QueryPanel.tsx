import React, { useState } from "react";
import Input from "./ui/Input";
import Button from "./ui/Button";
import Tab from "./ui/Tab";

export interface QueryPanelProps {
  /**
   * Handler for structured ask queries. Takes an intent, decision reference
   * and optional extra options.
   */
  onAsk: (
    intent: string,
    decisionRef: string,
    options?: Record<string, unknown>
  ) => Promise<void> | void;
  /**
   * Handler for natural language queries. Takes only the input text.
   */
  onQuery: (text: string) => Promise<void> | void;
  /**
   * Indicates whether a request is currently streaming.
   */
  isStreaming?: boolean;
}

const intents = [
  { value: "why_decision", label: "Why" },
  { value: "who_decided", label: "Who" },
  { value: "when_decided", label: "When" },
  { value: "chains", label: "Chains" },
];

/**
 * The QueryPanel provides a simple interface for submitting either a structured
 * ask or a natural language query. Users can toggle between modes and
 * provide the necessary inputs. Submission will call the supplied
 * onAsk/onQuery callbacks.
 */
export default function QueryPanel({ onAsk, onQuery, isStreaming }: QueryPanelProps) {
  const [mode, setMode] = useState<"ask" | "query">("ask");
  const [intent, setIntent] = useState<string>("why_decision");
  const [decisionRef, setDecisionRef] = useState<string>("");
  const [nlInput, setNlInput] = useState<string>("");

  /**
   * Switch between structured and natural query modes. Emits a debug log with
   * the previous and next values.
   */
  const handleModeChange = (newMode: "ask" | "query") => {
    if (newMode === mode) return;
    console.debug("[ui.memory.mode_switch]", { from: mode, to: newMode });
    setMode(newMode);
  };

  /**
   * Update the selected intent. Emits a debug log when the value changes.
   */
  const handleIntentChange = (val: string) => {
    console.debug("[ui.memory.intent_change]", { intent: val });
    setIntent(val);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    // prepare debug payload
    const payload = {
      mode,
      intent,
      rid: (window as any).__lastRid ?? null,
      nl_len: nlInput.trim().length,
      decision_ref: decisionRef.trim(),
    };
    console.debug("[ui.memory.submit]", payload);
    if (mode === "ask") {
      if (!decisionRef.trim()) return;
      await onAsk(intent, decisionRef.trim());
    } else {
      if (!nlInput.trim()) return;
      await onQuery(nlInput.trim());
    }
  };

  return (
    <div className="space-y-4">
      <div className="inline-flex rounded-full bg-black/30 backdrop-blur px-1 border border-vaultred/20">
        <Tab
          active={mode === "ask"}
          onClick={() => handleModeChange("ask")}
          className={mode !== "ask" ? "text-copy/80" : undefined}
        >
          Structured
        </Tab>
        <Tab
          active={mode === "query"}
          onClick={() => handleModeChange("query")}
          className={mode !== "query" ? "text-copy/80" : undefined}
        >
          Natural
        </Tab>
      </div>
      <form onSubmit={handleSubmit} className="space-y-4">
        {mode === "ask" ? (
          <>
            <div className="flex flex-col sm:flex-row sm:space-x-4 space-y-2 sm:space-y-0">
              <div className="flex-1">
                <label className="block text-sm font-medium text-copy mb-1">Decision reference</label>
                <Input
                  type="text"
                  value={decisionRef}
                  onChange={(e) => setDecisionRef(e.target.value)}
                  placeholder="e.g. panasonic-exit-plasma-2012"
                  required
                />
              </div>
              <div>
                <label className="block text-sm font-mono text-copy mb-1">Intent</label>
                <select
                  className="w-full px-3 py-2 rounded-md bg-surface text-copy border border-gray-700 focus:outline-none focus:ring-2 focus:ring-vaultred border-l border-vaultred/50 pl-3"
                  value={intent}
                  onChange={(e) => handleIntentChange(e.target.value)}
                >
                  {intents.map((opt) => (
                    <option key={opt.value} value={opt.value} className="bg-darkbg text-copy">
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </>
        ) : (
          <div>
            <label className="block text-sm font-medium text-copy mb-1">Query</label>
            <Input
              type="text"
              value={nlInput}
              onChange={(e) => setNlInput(e.target.value)}
              placeholder="Ask a question about a decision..."
              required
            />
          </div>
        )}
        <div className="flex justify-end">
          <Button type="submit" disabled={isStreaming}>
            {isStreaming ? "Streaming..." : "Submit"}
          </Button>
        </div>
      </form>
    </div>
  );
}