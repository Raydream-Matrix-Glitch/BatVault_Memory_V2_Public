// Centralized role definitions for FE ↔ policy alignment.
// Keep these in lockstep with `policy/roles/role-*.json`.
export const ROLES = ["analyst", "director", "ceo"] as const;
export type Role = typeof ROLES[number];

// Default role for unauthenticated/demo usage..
export const DEFAULT_ROLE: Role = "analyst";