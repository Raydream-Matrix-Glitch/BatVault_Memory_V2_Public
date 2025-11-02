// Generates typed OpenAPI types for Gateway & Memory into the FE.
// Guardrails: fail fast, narrow handling, no broad try/catch.
// - Prefers explicit OPENAPI_*_SPEC envs (file path or URL).
// - Falls back to local cached specs in ./openapi/{gateway.json,memory.json}.
// - As last resort, if CODEGEN_ALLOW_OFFLINE=1 and previously generated types exist
//   AND are not stubs, reuse them; otherwise fail closed.
// Structured logs are emitted as JSON with evt=codegen_openapi for CI audit.

import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import openapiTS, { astToString } from "openapi-typescript";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const feRoot = path.resolve(__dirname, "..");
const outDir = path.join(feRoot, "src", "sdk", "types");

const GATEWAY_BASE = process.env.VITE_GATEWAY_BASE || "http://localhost:8010";
const MEMORY_BASE  = process.env.VITE_MEMORY_BASE  || "http://localhost:8020";
const GW_SPEC_ENV  = process.env.OPENAPI_GATEWAY_SPEC || ""; // file path or URL
const MEM_SPEC_ENV = process.env.OPENAPI_MEMORY_SPEC  || ""; // file path or URL
const OFFLINE_OK   = process.env.CODEGEN_ALLOW_OFFLINE === "1";

const localGw = path.join(feRoot, "openapi", "gateway.json");
const localMem = path.join(feRoot, "openapi", "memory.json");
const generatedGw = path.join(outDir, "gateway.d.ts");
const generatedMem = path.join(outDir, "memory.d.ts");
const indexBarrel = path.join(outDir, "index.d.ts");

function log(obj) {
  // Deterministic-ish run_id: yyyymmddThhmmss + pid
  const now = new Date();
  const run_id = now.toISOString().replace(/[-:.]/g,"").slice(0,15) + "-" + process.pid;
  console.log(JSON.stringify({ evt: "codegen_openapi", run_id, ...obj }));
}

async function pathExists(p) {
  try { await fs.access(p); return true; } catch { return false; }
}

function isStub(content) {
  return /export\s+type\s+paths\s*=\s*Record<\s*string\s*,\s*any\s*>\s*;/.test(content);
}

// Ensure every operation has a unique operationId (required by openapi-typescript)
function ensureUniqueOperationIds(spec, label = "spec") {
  if (!spec || typeof spec !== "object" || !spec.paths) return spec;
  const HTTP_METHODS = new Set(["get","put","post","delete","options","head","patch","trace"]);
  const seen = new Set();
  const sanitize = (s) =>
    String(s)
      .replace(/[^\w]+/g, "_")  // non-word → _
      .replace(/^_+|_+$/g, "")  // trim _
      .replace(/_{2,}/g, "_");  // collapse __

  for (const [p, node] of Object.entries(spec.paths)) {
    if (!node || typeof node !== "object") continue;
    for (const [method, op] of Object.entries(node)) {
      if (!HTTP_METHODS.has(method) || !op || typeof op !== "object") continue;
      const base =
        op.operationId ||
        `${method}_${sanitize(p)}`; // e.g. put_memory__path_
      let id = base;
      let n = 1;
      while (seen.has(id)) {
        id = `${base}_${method}${n > 1 ? "_" + n : ""}`;
        n++;
      }
      if (op.operationId && op.operationId !== id) {
        console.warn(
          `[codegen] ${label}: rewrote duplicate operationId "${op.operationId}" at ${method.toUpperCase()} ${p} -> "${id}"`
        );
      } else if (!op.operationId) {
        console.warn(
          `[codegen] ${label}: synthesized operationId "${id}" for ${method.toUpperCase()} ${p}`
        );
      }
      op.operationId = id;
      seen.add(id);
    }
  }
  return spec;
}

async function resolveSpec(kind) {
  const env = kind === "gateway" ? GW_SPEC_ENV : MEM_SPEC_ENV;
  const base = kind === "gateway" ? GATEWAY_BASE : MEMORY_BASE;
  const local = kind === "gateway" ? localGw : localMem;
  const url = env || `${base}/openapi.json`;

  // Prefer explicit env
  if (env) { return env; }

  // Next, local cached file
  if (await pathExists(local)) { return local; }

  // Else, fall back to service URL (openapi-typescript will fetch)
  return url;
}

async function writeIndexBarrel() {
  const idx = `export type { paths as GatewayPaths } from './gateway';\nexport type { paths as MemoryPaths } from './memory';\n`;
  await fs.mkdir(outDir, { recursive: true });
  await fs.writeFile(indexBarrel, idx, "utf8");
}

async function main() {
  await fs.mkdir(outDir, { recursive: true });
  const gwSpec = await resolveSpec("gateway");
  const memSpec = await resolveSpec("memory");

  log({ phase: "resolve", gwSpec, memSpec });

  try {
    // Load local files OR fetch remote URLs so we can sanitize before codegen
    const loadSpec = async (p) => {
      // file:// URL
      if (typeof p === "string" && p.startsWith("file://")) {
        const fsPath = fileURLToPath(p);
        const raw = await fs.readFile(fsPath, "utf8");
        const obj = JSON.parse(raw);
        if (!obj || typeof obj !== "object" || !/^3\./.test(obj.openapi || "")) {
          throw new Error(`Unsupported schema format at ${p}, expected \`openapi: 3.x\``);
        }
        return obj;
      }
      // local filesystem path
      if (typeof p === "string" && !/^https?:\/\//.test(p)) {
        const raw = await fs.readFile(p, "utf8");
        const obj = JSON.parse(raw);
        if (!obj || typeof obj !== "object" || !/^3\./.test(obj.openapi || "")) {
          throw new Error(`Unsupported schema format at ${p}, expected \`openapi: 3.x\``);
        }
        return obj;
      }
      // http(s) URL – fetch it (Node 18+ has global fetch)
      if (typeof p === "string" && /^https?:\/\//.test(p)) {
        if (typeof fetch !== "function") {
          console.warn(`[codegen] No fetch available to load remote spec ${p}; passing URL through (cannot sanitize operationIds).`);
          return p; // fallback: let openapiTS fetch (may fail on dupes)
        }
        const res = await fetch(p);
        if (!res.ok) throw new Error(`Failed to fetch OpenAPI spec ${p} (${res.status})`);
        const obj = await res.json();
        if (!obj || typeof obj !== "object" || !/^3\./.test(obj.openapi || "")) {
          throw new Error(`Unsupported schema format at ${p}, expected \`openapi: 3.x\``);
        }
        return obj;
      }
      // already an object?
      return p;
    };
    const gwInputRaw  = await loadSpec(gwSpec);
    const memInputRaw = await loadSpec(memSpec);

    // Sanitize duplicate/missing operationIds before codegen
    const gwInput  = typeof gwInputRaw  === "object" ? ensureUniqueOperationIds(gwInputRaw,  "gateway") : gwInputRaw;
    const memInput = typeof memInputRaw === "object" ? ensureUniqueOperationIds(memInputRaw, "memory")  : memInputRaw;

    const gwAst  = await openapiTS(gwInput,  { exportType: true });
    const memAst = await openapiTS(memInput, { exportType: true });
    const gwDts  = typeof gwAst === "string" ? gwAst : astToString(gwAst);
    const memDts = typeof memAst === "string" ? memAst : astToString(memAst);
    if (!gwDts || !memDts) { throw new Error("empty d.ts output"); }

    await fs.writeFile(generatedGw, gwDts, "utf8");
    await fs.writeFile(generatedMem, memDts, "utf8");
    await writeIndexBarrel();

    log({ phase: "write", ok: true, files: [generatedGw, generatedMem, indexBarrel] });
    return;
  } catch (e) {
    log({ phase: "generate_failed", error: String(e), errorStack: e?.stack || "" });
    // If offline is allowed, reuse existing non-stub types
    if (OFFLINE_OK && (await pathExists(generatedGw)) && (await pathExists(generatedMem))) {
      const gwOld = await fs.readFile(generatedGw, "utf8");
      const memOld = await fs.readFile(generatedMem, "utf8");
      if (!isStub(gwOld) && !isStub(memOld)) {
        log({ phase: "reuse_existing", ok: true });
        await writeIndexBarrel();
        return;
      }
    }
    const msg =
      "OpenAPI type generation failed. Provide specs (OPENAPI_*_SPEC envs or ./openapi/*.json) " +
      "or set CODEGEN_ALLOW_OFFLINE=1 with existing non-stub types. " +
      `Underlying error: ${e?.message ?? e}`;
    throw new Error(msg);
  }
}

await main();