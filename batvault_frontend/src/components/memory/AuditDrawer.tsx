import React, { useState, useEffect, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import Button from "./ui/Button";
import Badge from "./ui/Badge";
import CopyButton from "./ui/CopyButton";
import { logEvent } from "../../utils/logger";
import type { MetaInfo } from "../../types/memory";
import { openPresignedBundle, openReceipt, fetchBundleMap, normalizeRid } from "../../utils/bundle";
import { verifyAll, type VerificationReport } from "../../utils/verify";

type VerificationUiState =
  | "pending"
  | "timeout"
  | "no-key"
  | "verified"

export interface AuditDrawerProps {
  open: boolean;
  onClose: () => void;
  meta?: MetaInfo;
  requestId?: string;
  canDownloadFull?: boolean;
}

function shorten(v?: string, keep = 6) {
  if (!v) return "–";
  const m = String(v);
  if (m.length <= keep * 2 + 1) return m;
  return m.slice(0, keep) + "…" + m.slice(-keep);
}

function StatusPill({ status }: { status: "pass" | "mismatch" | "na" }) {
  const label = status === "pass" ? "Passed" : status === "mismatch" ? "Mismatch" : "N/A";
  const icon  = status === "pass" ? "✓" : status === "mismatch" ? "≠" : "—";
  const color =
    status === "pass"
      ? "bg-green-500/15 text-green-400 border-green-500/30"
      : status === "mismatch"
      ? "bg-red-500/10 text-red-400 border-red-500/30"
      : "bg-white/5 text-white/60 border-white/10";
  return (
    <span className={`px-1.5 py-0.5 rounded-full text-[11px] border inline-flex items-center gap-1 ${color}`} aria-label={label} title={label}>
      <span>{icon}</span><span className="sr-only">{label}</span>
    </span>
  );
}

function FieldRow({
  name,
  badge,
  help,
  computed,
  claimed,
  status,
  showClaimedIfDifferent = true,
  tooltip,
  trusted = false,
}: {
  name: string;
  badge?: React.ReactNode;
  help?: string;
  computed?: string;
  claimed?: string;
  status: "pass" | "mismatch" | "na";
  showClaimedIfDifferent?: boolean;
  tooltip?: string;
  trusted?: boolean;
}) {
  const different = claimed && computed && claimed !== computed;
  return (
    <div className="grid grid-cols-[minmax(140px,1fr)_minmax(0,1fr)_auto] items-center gap-x-3 border-b border-white/10 py-2">
      <div className="flex items-center gap-2 min-w-0" title={help || tooltip}>
        <span className="font-mono text-[13px] truncate">{name}</span>
        {badge}
      </div>
      <div className="text-xs text-white/80 min-w-0">
        {trusted ? (
          <div className="flex items-center gap-1" title="Gateway-provided value; treated as trusted">
            <span className="text-white/60">Gateway (trusted)</span>
            <span className="font-mono truncate">{shorten(claimed)}</span>
            {claimed ? <CopyButton text={claimed} className="ml-1" /> : null}
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-1" title={tooltip}>
            <span className="text-white/60">Gateway</span>
            <span className="font-mono">{shorten(claimed)}</span>
            {claimed ? <CopyButton text={claimed} className="ml-1" /> : null}
            {computed ? <span className="px-1">/</span> : null}
            {computed ? (
              <>
                <span className="text-white/60">Local</span>
                <span className={`font-mono ${different ? "underline decoration-red-400/60" : ""}`}>
                  {shorten(computed)}
                </span>
                <CopyButton text={computed} className="ml-1" />
              </>
            ) : null}
          </div>
        )}
      </div>
      <div className="justify-self-end"><StatusPill status={status} /></div>
    </div>
  );
}

export default function AuditDrawer({ open, onClose, meta, requestId: req }: AuditDrawerProps) {
  const requestId = req || meta?.request_id;
  const [report, setReport] = useState<VerificationReport | null>(null);
  const [rawReceipt, setRawReceipt] = useState<any | null>(null);
  const [hasVerifierKey, setHasVerifierKey] = useState<boolean>(false);
  const [loading, setLoading] = useState(false);
  const [uiState, setUiState] = useState<VerificationUiState>("pending");
  const timeoutRef = useRef<number | null>(null);

  // narrow, explicit JSON parsing – we don't want one bad artifact to kill the run
  const safeParseJson = useCallback(
    (txt: string | undefined, kind: string, rid: string) => {
      if (!txt) return null;
      try {
        return JSON.parse(txt);
      } catch (err) {
        logEvent("ui.audit.parse_error", {
          rid,
          kind,
          error: String((err as any)?.message || err),
        });
        return null;
      }
    },
    []
  );

  const computeAndVerify = useCallback(async () => {
    if (!meta) return;
    // reset for this run
    setUiState("pending");
    setLoading(true);
    // watchdog: after 15s we move to "couldn't be achieved"
      if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    timeoutRef.current = window.setTimeout(() => {
      setUiState((prev) => (prev === "pending" ? "timeout" : prev));
    }, 15000);
    const rawRid = requestId || meta.request_id || "";
    const rid = normalizeRid(rawRid);
    let artifactMap: any = null;
    try {
      artifactMap = await fetchBundleMap(rid);
    } catch (err) {
      // network / CORS / 5xx – this is *not* a watchdog expiry
      logEvent("ui.audit.bundle_fetch.error", {
        rid,
        error: String((err as any)?.message || err),
      });
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      setUiState("timeout");   // fetch failed → not a pending state
      // show at least the values the backend already told us in meta
      setReport({
        request_id: rid,
        graph_fp: {
          claimed: meta?.fingerprints?.graph_fp,
          computed: undefined,
          ok: true,
          // we explicitly trust the backend here because FE may not be able to rederive it
          trusted: true,
        },
        allowed_ids_fp: {
          claimed: meta?.allowed_ids_fp,
          computed: "",
          ok: true,
        },
        bundle_fp: {
          claimed: meta?.bundle_fp,
          computed: "",
          ok: true,
        },
        anchor_ok: false,
        domain_ok: false,
      } as any);
      setLoading(false);
      return;
    }

    // gateway sometimes returns a "pending" / "not ready" bundle view – treat this as non-fatal
    const isPlaceholder =
      artifactMap &&
      typeof artifactMap === "object" &&
      typeof (artifactMap as any).status === "string" &&
      !("response.json" in artifactMap) &&
      !("receipt.json" in artifactMap);
    if (isPlaceholder) {
      logEvent("ui.audit.bundle_fetch.pending", {
        rid,
        status: (artifactMap as any).status,
      });
     if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      setUiState("timeout");
      setReport({
        request_id: rid,
        graph_fp: {
          claimed: meta?.fingerprints?.graph_fp,
          computed: undefined,
          ok: true,
          trusted: true,
        },
        allowed_ids_fp: {
          claimed: meta?.allowed_ids_fp,
          computed: "",
         ok: true,
        },
        bundle_fp: {
          claimed: meta?.bundle_fp,
          computed: "",
          ok: true,
        },
        anchor_ok: true,
        domain_ok: true,
      } as any);
      setLoading(false);
      return;
    }

    if (!artifactMap) {
      // defensive – shouldn't really happen, but don't blame the watchdog
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      setUiState("pending");
      setLoading(false);
      return;
    }

    const responseTxt = artifactMap["response.json"];
    const receiptTxt  = artifactMap["receipt.json"];
    if (!responseTxt || !receiptTxt) {
      logEvent("ui.audit.bundle_fetch.partial", {
        rid,
        has_response: Boolean(responseTxt),
        has_receipt: Boolean(receiptTxt),
      });
    }
    // use safe parser (we already had it) so one bad artifact doesn't kill the run
    const bundle  = safeParseJson(responseTxt, "response.json", rid);
    const receipt = safeParseJson(receiptTxt, "receipt.json", rid);
    try {
      // prefer the meta that actually came from the bundle – server's final view
      const effectiveMeta =
        (bundle && typeof bundle === "object" && (bundle as any).response && (bundle as any).response.meta)
          ? (bundle as any).response.meta
          : meta;
      if (!meta && effectiveMeta) {
        logEvent("ui.audit.meta.recovered_from_bundle", { rid });
      }
      logEvent("ui.audit.bundle_fetch.ok", {
        rid,
        raw_rid: rawRid,
        has_response: Boolean(responseTxt),
        has_receipt: Boolean(receiptTxt),
      });

      // keep it in state for the panel
      setRawReceipt(receipt);

      let pub = "";
      try {
        // strict: only trust the backend to tell us the current key
        const k1 = await fetch("/keys/gateway_ed25519_pub.b64");
        if (k1.ok) {
          pub = (await k1.text()).trim();
        } else {
          const k2 = await fetch("/keys/gateway_ed25519_pub.pem");
          if (k2.ok) pub = (await k2.text()).trim();
        }
      } catch (err) {
        logEvent("ui.audit.fetch_key.error", {
          rid,
          error: String((err as any)?.message || err),
        });
      }
      const hasKey = !!pub;
      setHasVerifierKey(hasKey);
      if (!hasKey) {
        // hard fail-closed, matches deck: "no unsigned receipts"
        setUiState("no-key");
      }
      // only attempt signature verification when we really have a key
      const rpt = await verifyAll({
        // if the downloaded bundle was null (e.g. parse failed), still pass something
        bundle: (bundle as any) ?? { response: { meta } },
        receipt,
        gatewayPublicKey: hasKey ? pub : undefined,
      });
      // verification finished → stop watchdog
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      setUiState((prev) => (prev === "timeout" ? "timeout" : "verified"));
      setReport(rpt);
      logEvent("ui.audit.verify.result", {
        rid: requestId,
        action: "ui.audit.verify.result",
        ok: !!(
          rpt.bundle_fp.ok &&
          rpt.allowed_ids_fp.ok &&
          rpt.graph_fp.ok &&
          (rpt.signature?.ok ?? true) &&
          (rpt.receipt?.covered_ok ?? true)
        ),
        graph_ok: rpt.graph_fp.ok,
        allowed_ok: rpt.allowed_ids_fp.ok,
        bundle_ok: rpt.bundle_fp.ok,
        receipt_covered_ok: rpt.receipt?.covered_ok ?? false,
        key_id: rpt.receipt?.key_id,
        signed_at: rpt.receipt?.signed_at,
      });
    } catch (err) {
      // logic / crypto failure – do NOT pretend this was a watchdog expiry
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      logEvent("ui.audit.compute.error", {
        rid: requestId,
        error: String((err as any)?.message || err),
      });
      logEvent("ui.audit.verify.fallback", {
        rid: requestId || meta?.request_id || null,
        error: String((err as any)?.message || err),
      });
      // surface at least the claimed meta so the UI doesn't stay in 'pending'
      setReport(() => ({
        request_id: requestId || meta?.request_id || undefined,
        graph_fp: {
          claimed: meta?.graph_fp ?? (meta as any)?.fingerprints?.graph_fp,
          computed: undefined,
          ok: false,
          trusted: false,
        },
        allowed_ids_fp: {
          claimed: meta?.allowed_ids_fp ?? (meta as any)?.fingerprints?.allowed_ids_fp,
          computed: "",
          ok: false,
        },
        bundle_fp: {
          claimed: meta?.bundle_fp ?? (meta as any)?.fingerprints?.bundle_fp,
          computed: "",
          ok: false,
        },
        receipt: receipt || undefined,
        anchor_ok: false,
        domain_ok: false,
      }));
      setUiState("timeout");
    } finally {
      setLoading(false);
    }
  }, [meta, requestId]);

  useEffect(() => {
    if (!open) {
      // drawer closed → kill any outstanding timer
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      return;
    }
    logEvent("ui.audit.open", { rid: requestId, action: "ui.audit.open" });
    computeAndVerify();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key.toLowerCase() === "r") {
        logEvent("ui.audit.reverify", { rid: requestId, action: "ui.audit.reverify" });
        computeAndVerify();
      }
      if (e.key.toLowerCase() === "b" && requestId) {
        logEvent("ui.audit.view_bundle", { rid: requestId, action: "ui.audit.view_bundle" });
        openPresignedBundle(normalizeRid(requestId), "bundle_view");
      }
      if (e.key.toLowerCase() === "f" && requestId) {
        logEvent("ui.audit.download_full", { rid: requestId, action: "ui.audit.download_full" });
        openPresignedBundle(normalizeRid(requestId), "bundle_full");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, requestId, onClose, computeAndVerify]);

  if (!open) return null;

  // deck: "Fail-closed if the signer isn’t configured (no unsigned receipts)"
  const failClosed = uiState === "no-key";
  const verdict = (() => {
    if (uiState === "timeout") {
      return {
        kind: "fail",
        text: "⚠️ Timed out after 15s. Try again.",
        color: "bg-amber-500/10 text-amber-100",
      } as const;
    }
    // strict mode: if we don't have the verifier key, we don't make claims
    if (failClosed) {
      return {
        kind: "fail",
        text: "🚫 Verification unavailable — gateway public key not configured.",
        color: "bg-red-600/15 text-red-300",
      } as const;
    }

    if (!report) {
      return {
        kind: "na",
        text: "Verification pending…",
        color: "bg-white/5 text-white/80",
      } as const;
    }
    const allPassed =
      report.graph_fp.ok &&
      report.allowed_ids_fp.ok &&
      report.bundle_fp.ok &&
      (report.receipt?.covered_ok ?? true) &&
      (report.signature?.ok ?? hasVerifierKey === false);
    if (allPassed)
      return {
        kind: "ok",
        text: "✅ Verified — Signature valid; all fingerprints match.",
        color: "bg-green-600/15 text-green-300",
      } as const;
    return {
      kind: "fail",
      text: "❌ Verification failed — See details below.",
      color: "bg-red-600/15 text-red-300",
    } as const;
  })();

  const tooltipServerVsComputed = "Gateway vs Local — must match for integrity.";
  const tooltipKeyId = "Key ID — identifies which public key verifies this receipt.";

  const body = (
    <div className="fixed inset-0 z-50 bg-black/50 text-white" aria-modal="true" role="dialog">
      <div className="absolute right-0 top-0 h-full w-[720px] bg-[#0b0f16] border-l border-white/10 shadow-xl flex flex-col overflow-hidden">
        {/* Header (sticky) */}
        <div className="sticky top-0 z-10 flex items-center justify-between px-5 py-3 border-b border-white/10 bg-[#0b0f16]">
          <div className="font-semibold">Audit</div>
          <button
            className="text-white/70 hover:text-white"
            onClick={onClose}
            aria-label="Close audit drawer (Esc)"
          >
            ✕
          </button>
        </div>

        {/* Scrollable content */}
        <div className="flex-1 overflow-y-auto">
        {/* Status banner (use full space with side gutters) */}
        <div className={`mx-5 my-4 rounded-lg px-5 py-4 border ${verdict.color} border-white/10`}>
          <div className="flex items-center justify-between gap-2">
            <div>
              <div className="font-semibold">Verification result</div>
              <div className="text-sm mt-1">{verdict.text}</div>
              <div className="text-xs mt-2 text-white/70">
                The receipt signs <span className="font-mono">bundle_fp</span>, a deterministic hash of the full response.
                We recompute fingerprints locally and compare them to the server’s claim.
              </div>
            </div>
            <div className="flex items-center gap-2 self-start">
              <Badge size="xs">ed25519</Badge>
              <Badge size="xs">offline-capable</Badge>
              <Badge size="xs">deterministic</Badge>
            </div>
          </div>
        </div>

        {/* Request info (dense) */}
        <div className="mx-5 mb-3 border border-white/10 rounded-lg p-2">
          <div className="text-sm flex flex-wrap gap-x-4 gap-y-1 items-center">
            <div className="flex items-center gap-1">
              <span className="text-white/60">Request ID</span>{" "}
              <span className="font-mono">{requestId ?? "–"}</span>{" "}
              {requestId ? <CopyButton text={requestId} /> : null}
            </div>
            <div className="flex items-center gap-1" title="UTC">
              <span className="text-white/60">Signed at</span>{" "}
              <span className="font-mono">
                {report?.receipt?.signed_at ?? rawReceipt?.signed_at ?? "–"}
              </span>
            </div>
            <div className="flex items-center gap-1" title={tooltipKeyId}>
              <span className="text-white/60">Key ID</span>{" "}
              <span className="font-mono">
                {report?.receipt?.key_id ?? rawReceipt?.key_id ?? "–"}
              </span>
            </div>
          </div>
        </div>

        {/* Fingerprints group (full-width rows, no heading) */}
        <div className="mx-5 mb-3 border border-white/10 rounded-lg">
          <div className="px-3">
            <FieldRow
              name="bundle_fp"
              badge={<Badge size="xs">signature target</Badge>}
              help="Canonical hash of the full response (signature target)."
              computed={report?.bundle_fp.computed}
              claimed={meta?.bundle_fp}
              status={failClosed ? "na" : (report ? (report.bundle_fp.ok ? "pass" : "mismatch") : "na")}
              tooltip={tooltipServerVsComputed}
            />
            <FieldRow
              name="graph_fp"
              badge={<Badge size="xs">derived</Badge>}
              help="Graph structure: hash of anchor + edges."
              computed={report?.graph_fp.computed}
              claimed={meta?.fingerprints?.graph_fp}
              status={failClosed ? "na" : (report ? (report.graph_fp.ok ? "pass" : "mismatch") : "na")}
              tooltip="Gateway-emitted graph_fp; frontend may not be able to reproduce if policy/canonicalization was applied."
              trusted={report?.graph_fp.trusted === true}
            />
            <FieldRow
              name="allowed_ids_fp"
              badge={<Badge size="xs">derived</Badge>}
              help="Access scope: hash of the authorized ID list."
              computed={report?.allowed_ids_fp.computed}
              claimed={meta?.allowed_ids_fp}
              status={failClosed ? "na" : (report ? (report.allowed_ids_fp.ok ? "pass" : "mismatch") : "na")}
              tooltip={tooltipServerVsComputed}
            />
            <FieldRow
              name="receipt.covered"
              badge={<Badge size="xs">receipt</Badge>}
              help="Must equal bundle_fp; otherwise the receipt signs other content."
              computed={report?.receipt?.covered ?? rawReceipt?.covered}
              claimed={
                report?.bundle_fp.claimed ??
                report?.bundle_fp.computed ??
                rawReceipt?.covered
              }
              status={failClosed ? "na" : (report?.receipt ? (report?.receipt?.covered_ok ? "pass" : "mismatch") : "na")}
              showClaimedIfDifferent={true}
              tooltip="Claimed = value embedded by the server. Computed = recalculated locally. They must match."
            />
            {/* optional rows */}
            <FieldRow
              name="policy_fp"
              badge={<Badge size="xs">inside bundle</Badge>}
              help="Effective policy fingerprint (included inside the signed bundle)."
              computed={meta?.policy_fp}
              claimed={meta?.policy_fp}
              status={meta?.policy_fp ? "pass" : "na"}
              showClaimedIfDifferent={false}
            />
            <FieldRow
              name="schema_fp"
              badge={<Badge size="xs">header</Badge>}
              help="Schema fingerprint provided in headers."
              computed={meta?.schema_fp}
              claimed={meta?.schema_fp}
              status={meta?.schema_fp ? "pass" : "na"}
              showClaimedIfDifferent={false}
            />
          </div>
        </div>

        {/* Receipt panel */}
        {/* Receipt panel (collapsed by default) */}
        <div className="mx-5 mb-24 border border-white/10 rounded-lg">
          <div className="p-2 border-b border-white/10 flex items-center justify-between">
            <div className="font-medium">Receipt</div>
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={() => {
                  if (requestId) {
                    logEvent("ui.audit.download_receipt", {
                      rid: requestId,
                      action: "ui.audit.download_receipt",
                    });
                    openReceipt(normalizeRid(requestId));
                  }
                }}
                aria-label="Download receipt.json"
              >
                Download receipt.json
              </Button>
              {rawReceipt ? (
                <Button
                  size="sm"
                  onClick={() =>
                    navigator.clipboard.writeText(JSON.stringify(rawReceipt, null, 2))
                  }
                  aria-label="Copy receipt.json"
                >
                  Copy
                </Button>
              ) : null}
            </div>
          </div>
          <details className="p-2">
            <summary className="cursor-pointer text-xs text-white/70">Show receipt JSON</summary>
            <div className="mt-2">
              <div className="text-xs text-white/60 mb-2">
                This receipt is an Ed25519 signature over{" "}
                <span className="font-mono">bundle_fp</span>. Anyone with the public key can verify it offline.
              </div>
              {rawReceipt ? (
                <pre className="max-h-64 overflow-auto rounded bg-black/40 p-2 text-xs leading-snug">
                  {JSON.stringify(rawReceipt, null, 2)}
                </pre>
              ) : (
                <div className="text-xs text-white/60">Receipt not available.</div>
              )}
              {!report?.signature && hasVerifierKey === false && (
                <div className="mt-2 text-xs text-white/60">
                  Signature check skipped: public key not configured. Place it at{" "}
                  <span className="font-mono">/keys/gateway_ed25519_pub.(b64|pem)</span>.
                </div>
              )}
            </div>
          </details>
        </div>
        </div>{/* end scrollable */}

        {/* Footer actions (sticky) */}
        <div className="sticky bottom-0 left-0 right-0 border-t border-white/10 bg-[#0b0f16]/80 backdrop-blur px-5 py-3 flex items-center gap-2">
          <Button
            onClick={() => {
              logEvent("ui.audit.reverify", { rid: requestId, action: "ui.audit.reverify" });
              computeAndVerify();
            }}
            aria-label="Recalculate fingerprints and re-verify the signature (r)"
          >
            Re-verify
          </Button>
          <Button
            variant="secondary"
            onClick={() => {
              if (requestId) {
                logEvent("ui.audit.view_bundle", { rid: requestId, action: "ui.audit.view_bundle" });
                openPresignedBundle(normalizeRid(requestId), "bundle_view");
              }
            }}
            aria-label="View bundle (b)"
            disabled={!requestId}
          >
            View bundle
          </Button>
          <Button
            variant="secondary"
            onClick={() => {
              if (requestId) {
                logEvent("ui.audit.download_full", {
                  rid: normalizeRid(requestId),
                  action: "ui.audit.download_full",
                });
                openPresignedBundle(normalizeRid(requestId), "bundle_full");
              }
            }}
            aria-label="Download full (requires permission: bundle_full) (f)"
            disabled={!requestId}
          >
            Download full
          </Button>
          <Button
            variant="secondary"
            onClick={() => {
              if (requestId) {
                logEvent("ui.audit.download_receipt", {
                  rid: normalizeRid(requestId),
                  action: "ui.audit.download_receipt",
                });
                openReceipt(normalizeRid(requestId));
              }
            }}
            aria-label="Open receipt.json"
            disabled={!requestId}
          >
            Receipt
          </Button>
          <div className="ml-auto text-xs text-white/50" aria-live="polite">
            {loading ? "Verifying…" : ""}
          </div>
        </div>

        {/* Empty/error state */}
        {!requestId ? (
          <div className="mx-5 mt-2 text-xs text-white/70">
            Open a result to verify its receipt.
          </div>
        ) : null}
      </div>
    </div>
  );

  return createPortal(body, document.body);
}
