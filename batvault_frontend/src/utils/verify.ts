import { graphFp, allowedIdsFp, bundleFp, verifyReceipt } from "../../packages/fp-js/src";
import type { BundlesExecSummary } from "../types/generated/bundles.exec_summary";
import { DOMAIN_RE, ANCHOR_RE, isAnchor } from "../generated/grammar";
import { logEvent } from "../utils/logger";
// NOTE: FE verification must use the *canonical* edge set (unoriented, deduped) to match Gateway. In case of mismatch we trust the backend, because the operations performed may be unclear to the frontend. 

export type VerificationReport = {
  request_id?: string;
  graph_fp: { claimed?: string; computed?: string; ok: boolean; trusted?: boolean };
  allowed_ids_fp: { claimed?: string; computed: string; ok: boolean };
  bundle_fp: { claimed?: string; computed: string; ok: boolean };
  signature?: { ok: boolean };
  receipt?: { covered?: string; covered_ok?: boolean; key_id?: string; signed_at?: string };
  anchor_ok: boolean;
  domain_ok: boolean;
};

/** Validate anchor & domain patterns via generated grammar.ts */
export function validateAnchorAndDomain(anchorId: unknown): { anchor_ok: boolean; domain_ok: boolean } {
  // Accept legacy shapes: string | { id: string } | null | undefined.
  const s =
    typeof anchorId === "string"
      ? anchorId
      : anchorId && typeof anchorId === "object" && typeof (anchorId as any).id === "string"
        ? (anchorId as any).id
        : "";
  if (!s) {
    try {
      logEvent("ui.verify.anchor.invalid_shape", {
        anchor: anchorId === undefined ? "undefined" : anchorId === null ? "null" : typeof anchorId,
      });
    } catch { /* non-fatal */ }
  }
  const dom = s ? s.split("#", 1)[0]! : "";
  return {
    anchor_ok: Boolean(s) && isAnchor(s),
    domain_ok: Boolean(dom && DOMAIN_RE.test(dom) && ANCHOR_RE.test(s)),
  };
}

export async function verifyAll(args: {
  bundle: BundlesExecSummary;
  receipt?: { sig: string } | null;
  gatewayPublicKey?: string | Uint8Array;
}): Promise<VerificationReport> {
  const resp: any = (args.bundle as any)?.response ?? {};
  const rawMeta: any = resp.meta ?? {};
  // bundle meta is authoritative; headers / FE-propagated meta are just fallbacks.
  // normalise both "flat" and "fingerprints.*" shapes into one object so the UI
  // doesn't have to guess.
  const meta: any = {
    ...rawMeta,
    fingerprints: {
      ...(rawMeta.fingerprints ?? {}),
      // gateway often emits these flat:
      graph_fp: rawMeta.graph_fp ?? rawMeta.fingerprints?.graph_fp,
      bundle_fp: rawMeta.bundle_fp ?? rawMeta.fingerprints?.bundle_fp,
      allowed_ids_fp:
        rawMeta.allowed_ids_fp ?? rawMeta.fingerprints?.allowed_ids_fp,
    },
  };

  const claimedGraphFp =
    meta.graph_fp /* flat, bundle-authored */ ??
    meta.fingerprints?.graph_fp /* old shape */ ??
    undefined;
  const claimedBundleFp =
    meta.bundle_fp ?? meta.fingerprints?.bundle_fp ?? undefined;
  const claimedAllowedIdsFp =
    meta.allowed_ids_fp ?? meta.fingerprints?.allowed_ids_fp ?? undefined;
  // ---- canonical anchor ----------------------------------------------------
  // resp.anchor may be an object { id, ... } — ensure we pass a string anchor id
  const anchorFromMeta =
    typeof meta?.anchor_id === "string" ? meta.anchor_id : "";
  const anchorFromResp =
    typeof resp?.anchor === "string"
      ? resp.anchor
      : (resp?.anchor && typeof resp.anchor === "object" && typeof resp.anchor.id === "string"
          ? resp.anchor.id
          : "");
  const canonicalAnchor = anchorFromMeta || anchorFromResp || "";

  // ---- canonical edges -----------------------------------------------------
  // Prefer the canonical evidence graph (this is what Gateway recomputed + signed).
  // Fall back to response graph, but strip display-only fields like "orientation"
  // so the hash stays stable and matches backend FP semantics.
  const evidenceEdges = (resp as any)?.evidence?.graph?.edges;
  const responseEdges = Array.isArray(resp?.graph?.edges)
    ? resp.graph.edges.map(({ orientation, ...rest }: any) => rest)
    : [];
  const canonicalEdges = evidenceEdges ?? responseEdges;

  // ---- graph fp (FE attempt) -----------------------------------------------
  let computedGraphFp: string | null = null;
  try {
    computedGraphFp = await graphFp(canonicalAnchor, canonicalEdges);
  } catch {
    computedGraphFp = null;
  }
  const canCompare = Boolean(claimedGraphFp) && Boolean(computedGraphFp);
  const graphsMatch = canCompare && claimedGraphFp === computedGraphFp;
  const graphTrusted = Boolean(claimedGraphFp) && !graphsMatch;
  const computedAllowed = await allowedIdsFp(meta.allowed_ids || []);
  const computedBundle = await bundleFp(resp);
  const sigOk =
    args.receipt && args.gatewayPublicKey
      ? (await verifyReceipt({ response: resp, receipt: args.receipt, publicKey: args.gatewayPublicKey })).ok
      : undefined;
  const rec: any = args?.receipt ?? null;
  const covered = rec?.covered || undefined;
  const coveredOk = covered ? covered === computedBundle : undefined;
  const shape = validateAnchorAndDomain(canonicalAnchor);
  const allowedIdsOk =
    (claimedAllowedIdsFp ?? "") === computedAllowed;
  try {
    logEvent("ui.verify.graph_fp", {
      anchor_claimed: anchorFromResp,
      anchor_used: canonicalAnchor,
      edges_evidence: Array.isArray(evidenceEdges) ? evidenceEdges.length : 0,
      edges_response: Array.isArray(resp?.graph?.edges) ? resp.graph.edges.length : 0,
      edges_used: canonicalEdges.length,
      // surface where the numbers came from – helps with bundle vs header drift
      meta_source: "bundle",
      bundle_claimed_graph_fp: claimedGraphFp || null,
      bundle_claimed_bundle_fp: claimedBundleFp || null,
      claimed_fp: claimedGraphFp || null,
      computed_fp: computedGraphFp,
      trusted: graphTrusted,
    });
  } catch {
    // best-effort logging
  }
  return {
    request_id: meta?.request_id || undefined,
    graph_fp: {
      claimed: claimedGraphFp,
      computed: computedGraphFp ?? undefined,
      ok: graphsMatch || graphTrusted,
      trusted: graphTrusted,
    },
    allowed_ids_fp: {
      claimed: claimedAllowedIdsFp,
      computed: computedAllowed,
      ok: allowedIdsOk,
    },
    bundle_fp: {
      claimed: claimedBundleFp,
      computed: computedBundle,
      ok: (claimedBundleFp ?? "") === computedBundle,
    },
    // surface signature result ONLY if we actually managed to run it
    signature: sigOk === undefined ? undefined : { ok: sigOk },
    // but surface receipt fields whenever the gateway sent them – the drawer wants to show
    // “Signed at” / “Key ID” even if the signature step failed or key was missing.
    receipt: rec
      ? {
          covered,
          covered_ok: coveredOk,
          key_id: rec?.key_id ?? undefined,
          signed_at: rec?.signed_at ?? undefined,
        }
      : undefined,
    ...shape,
  };
}