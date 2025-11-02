// Centralized role definitions for FE ↔ policy alignment.
// Keep these in lockstep with `policy/roles/role-*.json`.
export const ROLES = ["ceo", "manager", "analyst"] as const;
export type Role = typeof ROLES[number];

// Default role selected
export const DEFAULT_ROLE: Role = "ceo";