import React, { useState } from "react";
import Input from "./ui/Input";
import Button from "./ui/Button";
import Tab from "./ui/Tab";
import { logEvent } from "../../utils/logger";

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


/**
 * The QueryPanel provides a simple interface for submitting either a structured
 * ask or a natural language query. Users can toggle between modes and
 * provide the necessary inputs. Submission will call the supplied
 * onAsk/onQuery callbacks.
 */
export default function QueryPanel({ onAsk, onQuery, isStreaming }: QueryPanelProps) {
  const [mode, setMode] = useState<"ask" | "query">("ask");
  const [decisionRef, setDecisionRef] = useState<string>("");
  const [nlInput, setNlInput] = useState<string>("");

  /**
   * Switch between structured and natural query modes. Emits a debug log with
   * the previous and next values.
   */
  const handleModeChange = (newMode: "ask" | "query") => {
    if (newMode === mode) return;
    console.debug("[ui.memory.mode_switch]", { from: mode, to: newMode });
    try { logEvent("ui.memory.mode_switch", { from: mode, to: newMode }); } catch { /* ignore */ }
    setMode(newMode);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    // prepare debug payload
    const payload = {
      mode,
      rid: (window as any).__lastRid ?? null,
      nl_len: nlInput.trim().length,
      decision_ref: decisionRef.trim(),
    };
    try {
      logEvent("ui_memory_trace_clicked", payload);
    } catch { /* ignore logging errors */ }
    console.debug("[ui.memory.submit]", payload);
    if (mode === "ask") {
      if (!decisionRef.trim()) return;
      await onAsk("why_decision", decisionRef.trim());
    } else {
      if (!nlInput.trim()) return;
      await onQuery(nlInput.trim());
    }
  };

  return (
    <div className="space-y-4">
      <div className="inline-flex rounded-md bg-black/30 backdrop-blur p-1 border border-vaultred/20">
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
          <div className="flex items-stretch gap-2">
            <Input
              type="text"
              value={decisionRef}
              onChange={(e) => setDecisionRef(e.target.value)}
              placeholder="Enter a decision reference (e.g., panasonic-exit-plasma-2012)"
              required
              className="flex-1"
            />
            <Button type="submit" disabled={isStreaming}>
              {isStreaming ? "Streaming..." : "Trace"}
            </Button>
          </div>
        ) : (
          <div className="flex items-stretch gap-2">
            <Input
              type="text"
              value={nlInput}
              onChange={(e) => setNlInput(e.target.value)}
              placeholder="Ask a question about a decision..."
              required
              className="flex-1"
            />
            <Button type="submit" disabled={isStreaming}>
              {isStreaming ? "Streaming..." : "Trace"}
            </Button>
          </div>
        )}
      </form>
    </div>
  );
}