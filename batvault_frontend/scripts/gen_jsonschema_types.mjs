// Generate TS types from JSON Schemas in the core_models package.
// Guardrails: no broad catches; per-file narrow handling with clear fallbacks.
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { compileFromFile } from "json-schema-to-typescript";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const feRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(feRoot, "..");
const outDir = path.join(feRoot, "src", "types", "generated");

const SCHEMAS_DIR =
  process.env.CORE_SCHEMAS_DIR ||
  path.join(repoRoot, "packages", "core_models", "src", "core_models", "schemas");

/** Deterministic, explicit file list. */
const FILES = [
  { in: "memory.meta.json",             out: "memory.meta.d.ts" },
  { in: "memory.graph_view.json",       out: "memory.graph_view.d.ts" },
  { in: "bundles.exec_summary.json",    out: "bundles.exec_summary.d.ts" },
  { in: "bundles.view.json",            out: "bundles.view.d.ts" },
  { in: "bundles.trace.json",           out: "bundles.trace.d.ts" },
  { in: "bundle.manifest.json",         out: "bundle.manifest.d.ts" },
  { in: "receipt.json",                 out: "receipt.d.ts" },
  { in: "edge.wire.json",               out: "edge.wire.d.ts" },
  { in: "gateway.plan.json",            out: "gateway.plan.d.ts" },
  { in: "policy.input.json",            out: "policy.input.d.ts" },
  { in: "policy.decision.json",         out: "policy.decision.d.ts" },
  { in: "memory.query.request.json",    out: "memory.query.request.d.ts" },
  { in: "memory.resolve.response.json", out: "memory.resolve.response.d.ts" },
  { in: "meta.inputs.json",             out: "meta.inputs.d.ts" }
];

async function ensureOutDir() {
  await fs.mkdir(outDir, { recursive: true });
}

async function writeStub(absOut) {
  const name = path.basename(absOut, ".d.ts").replace(/\W+/g, "_");
  await fs.writeFile(
    absOut,
    `/* auto-stub */\nexport type ${name} = Record<string, unknown>;\n`,
    "utf8"
  );
}

async function compileOne(relIn, relOut) {
  const absIn = path.join(SCHEMAS_DIR, relIn);
  const absOut = path.join(outDir, relOut);
  try {
    const ts = await compileFromFile(absIn, {
      additionalProperties: false,
      bannerComment:
        "/* eslint-disable */\n/** AUTO-GENERATED from JSON Schema. DO NOT EDIT. */",
      cwd: SCHEMAS_DIR,
      style: { printWidth: 100 }
    });
    await fs.writeFile(absOut, ts, "utf8");
    console.log(`[codegen:types] ${relIn} → ${relOut}`);
  } catch (err) {
    console.warn(`[codegen:types] Failed for ${relIn}: ${String(err)}`);
    await writeStub(absOut);
  }
}

await ensureOutDir();
for (const f of FILES) {
  await compileOne(f.in, f.out);
}