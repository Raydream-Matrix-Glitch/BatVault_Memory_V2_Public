import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));

const REPO_ROOT = join(__dirname, '..');
const ANCHOR_SCHEMA = join(REPO_ROOT, 'packages', 'core_models', 'src', 'core_models', 'schemas', 'anchor.json');
const DOMAIN_SCHEMA = join(REPO_ROOT, 'packages', 'core_models', 'src', 'core_models', 'schemas', 'domain.json');
const OUT_FILE = join(REPO_ROOT, 'batvault_frontend', 'src', 'generated', 'grammar.ts');

function loadPattern(p) {
  const j = JSON.parse(readFileSync(p, 'utf8'));
  if (!j || typeof j.pattern !== 'string' || !j.pattern.startsWith('^') || !j.pattern.endsWith('$')) {
    throw new Error(`Schema at ${p} must contain a ^...$ 'pattern' string`);
  }
  return j.pattern;
}

const anchorPattern = loadPattern(ANCHOR_SCHEMA);
const domainPattern = loadPattern(DOMAIN_SCHEMA);

const header = `/* AUTO-GENERATED FILE - DO NOT EDIT.
 * Derived from packages/core_models/src/core_models/schemas/{anchor,domain}.json
 * Generation: scripts/gen_anchor_regex.mjs
 */
`;

const body = `
/** Canonical domain/anchor regular expressions (generated from JSON Schema) */
export const DOMAIN_RE = new RegExp(${JSON.stringify(domainPattern)});
export const ANCHOR_RE = new RegExp(${JSON.stringify(anchorPattern)});
export function isAnchor(value: string | null | undefined): boolean {
  return ANCHOR_RE.test(String(value ?? '').trim());
}
`;

mkdirSync(dirname(OUT_FILE), { recursive: true });
writeFileSync(OUT_FILE, header + body, 'utf8');
console.log('[gen_anchor_regex] wrote', OUT_FILE);