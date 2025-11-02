import { z } from "zod";

// Signing config as advertised by Gateway (/config)
export const PublicSigning = z.object({
  alg: z.literal("Ed25519"),
  public_key_b64: z.string().min(1).optional().default(undefined),
});

export const PublicEndpoints = z.object({
  query: z.string().default("/v3/query"),
  bundles: z.string().default("/v3/bundles"),
  // future: ops, admin etc.
});

export const PublicTimeouts = z.object({
  search: z.number().int().nonnegative(),
  expand: z.number().int().nonnegative(),
  enrich: z.number().int().nonnegative(),
  validate: z.number().int().nonnegative(),
});

export const PublicBundles = z.object({
  bundle_archives: z.array(z.string()).optional(),
  bundle_view_files: z.array(z.string()).optional(),
});

export const PublicConfig = z.object({
  gateway_base: z.string().url().or(z.string().min(1)),
  memory_base: z.string().url().or(z.string().min(1)),
  endpoints: PublicEndpoints,
  timeouts_ms: PublicTimeouts,
  // optional bundle metadata (new)
  bundle_archives: PublicBundles.shape.bundle_archives.optional(),
  bundle_view_files: PublicBundles.shape.bundle_view_files.optional(),
  signing: PublicSigning,
});
export type PublicConfig = z.infer<typeof PublicConfig>;