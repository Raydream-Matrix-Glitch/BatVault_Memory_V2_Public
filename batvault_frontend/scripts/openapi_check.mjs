// batvault_frontend/scripts/openapi_check.mjs
import { readFileSync } from "node:fs";

for (const name of ["gateway", "memory"]) {
  const p = `openapi/${name}.json`;
  let raw;
  try {
    raw = readFileSync(p, "utf8");
  } catch {
    console.error(`${p}: not found`);
    process.exit(2);
  }
  let x;
  try {
    x = JSON.parse(raw);
  } catch {
    console.error(`${p}: not valid JSON`);
    process.exit(2);
  }
  if (typeof x.openapi !== "string" || !x.openapi.startsWith("3.")) {
    console.error(`${p}: missing or invalid top-level "openapi" (got ${x.openapi})`);
    process.exit(2);
  }
  console.log(p, "OK", x.openapi);
}
