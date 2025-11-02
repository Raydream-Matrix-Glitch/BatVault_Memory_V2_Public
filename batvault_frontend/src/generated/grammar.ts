/* AUTO-GENERATED FILE - DO NOT EDIT.
 * Derived from packages/core_models/src/core_models/schemas/{anchor,domain}.json
 * Generation: scripts/gen_anchor_regex.mjs
 */

/** Canonical domain/anchor regular expressions (generated from JSON Schema) */
export const DOMAIN_RE = new RegExp("^(?:[a-z0-9]+(?:-[a-z0-9]+)*)(?:/(?:[a-z0-9]+(?:-[a-z0-9]+)*))*$");
export const ANCHOR_RE = new RegExp("^([a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*)#[a-z0-9._:-]+$");
export function isAnchor(value: string | null | undefined): boolean {
  return ANCHOR_RE.test(String(value ?? '').trim());
}
