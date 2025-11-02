// batvault_frontend/src/sdk/orval.config.ts
import path from "node:path";
import process from "node:process";
import type { Config } from "orval";

const cwd = process.cwd();

const isUrl = (s?: string) => !!s && /^(https?|file):\/\//.test(s);

const resolveSpec = (envVal: string | undefined, fallbackRel: string) => {
  // If an absolute/URL-like string is provided, pass it through
  if (isUrl(envVal)) return envVal as string;
  // Use env var or fallback, resolved to an absolute filesystem path
  const rel = envVal ?? fallbackRel;
  return path.resolve(cwd, rel);
};

// Prefer env-provided locations (set by your npm script), otherwise use ./openapi/*.json
const GW_SPEC  = resolveSpec(process.env.OPENAPI_GATEWAY_SPEC, "openapi/gateway.json");
const MEM_SPEC = resolveSpec(process.env.OPENAPI_MEMORY_SPEC,  "openapi/memory.json");

const config: Config = {
  "gateway-client": {
    input: { target: GW_SPEC },
    output: {
      target: "src/sdk/gateway.client.ts",
      client: "react-query",
    },
  },
  "memory-client": {
    input: { target: MEM_SPEC },
    output: {
      target: "src/sdk/memory.client.ts",
      client: "react-query",
    },
  },
};

export default config;
