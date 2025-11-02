import React from "react";
import CopyButton from "./ui/CopyButton";
import Button from "./ui/Button";

type MetaLike = { bundle_fp?: string; policy_fp?: string; snapshot_etag?: string; request_id?: string };
type Props = {
  meta?: MetaLike;
  onVerify?: () => void;
  onDownloadView?: () => void;
  onDownloadFull?: () => void;
  onDownloadReceipt?: () => void;
  canDownloadFull?: boolean;
};

function shorten(v?: string, keep = 6) {
  if (!v) return "–";
  if (v.length <= keep * 2 + 1) return v;
  return `${v.slice(0, keep)}…${v.slice(-keep)}`;
}

export default function ReceiptBar({ meta, onVerify, onDownloadView, onDownloadFull, onDownloadReceipt, canDownloadFull }: Props) {
  return (
    <div className="w-full rounded-xl border border-vaultred/40 bg-black/30 px-3 py-2">
      <div className="flex flex-wrap items-center gap-3">
        <Field label="bundle_fp" value={meta?.bundle_fp} />
        <Field label="policy_fp" value={meta?.policy_fp} />
        <Field label="snapshot_etag" value={meta?.snapshot_etag} />
        <div className="ml-auto flex items-center gap-2">
          {onVerify && <Button variant="secondary" className="text-xs" onClick={onVerify}>Verify</Button>}
          {onDownloadView && (
            <Button variant="secondary" className="text-xs" onClick={onDownloadView}>View bundle</Button>
          )}
          {onDownloadReceipt && (
            <Button variant="secondary" className="text-xs" onClick={onDownloadReceipt}>Receipt</Button>
          )}
          {onDownloadFull && canDownloadFull && (
            <Button variant="secondary" className="text-xs" onClick={onDownloadFull}>Download full</Button>
          )}
        </div>
      </div>
      <details className="mt-2 text-xs opacity-80">
        <summary className="cursor-pointer">Offline verification (CLI)</summary>
        <div className="mt-2 space-y-1">
          <div>1) Download <code>response.json</code> and <code>receipt.json</code> from the bundle.</div>
          <div>2) Get the gateway public key (<code>/keys/gateway_ed25519_pub.pem</code> in this app).</div>
          <pre className="bg-black/40 rounded p-2 overflow-x-auto">
{`python scripts/verify_receipt.py response.json receipt.json --pubkey public/keys/gateway_ed25519_pub.pem`}
          </pre>
        </div>
      </details>
    </div>
  );
}

function Field({ label, value }: { label: string; value?: string }) {
  return (
    <div className="flex items-center gap-1">
      <span className="text-muted text-[11px]">{label}:</span>
      <span className="font-mono text-[11px] text-white/90">{shorten(value)}</span>
      {value ? <CopyButton text={value} className="ml-1" /> : null}
    </div>
  );
}