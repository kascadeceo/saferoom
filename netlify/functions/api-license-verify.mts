// POST /api/license/verify — the only endpoint the SafeRoom CLI ever calls
// (one explicit POST during `saferoom license activate <KEY>`).
//
// Request:  {"key": "SR-..."}
// Response: {"valid": true, "tier": "pro-mo"} | {"valid": false, ...}
//
// Stateless by design (goodwill licensing, not DRM): the key's own
// HMAC-SHA256 signature + expiry is the whole truth. No key data is stored
// and keys are never written to function logs.
import type { Config } from "@netlify/functions";
import { verifyLicense } from "./lib/license.mjs";

declare const Netlify: { env: { get(name: string): string | undefined } };

const JSON_HEADERS = { "content-type": "application/json" };

export default async (req: Request) => {
  if (req.method !== "POST") {
    return Response.json({ valid: false, error: "POST only" },
      { status: 405, headers: JSON_HEADERS });
  }
  let body: { key?: unknown };
  try {
    body = await req.json();
  } catch {
    return Response.json({ valid: false, error: "body must be JSON" },
      { status: 400, headers: JSON_HEADERS });
  }
  const secret = Netlify.env.get("LICENSE_SECRET");
  if (!secret) {
    return Response.json({ valid: false, error: "license service not configured" },
      { status: 500, headers: JSON_HEADERS });
  }
  const today = new Date().toISOString().slice(0, 10);
  const result = await verifyLicense(String(body.key ?? ""), secret, today);
  return Response.json(
    { valid: result.valid, tier: result.valid ? result.payload.tier : null },
    { headers: JSON_HEADERS });
};

export const config: Config = {
  path: "/api/license/verify",
  method: ["POST"],
  // Brute-forcing an HMAC-SHA256 signature at 20 req/min is already hopeless;
  // this just keeps the endpoint from being hammered.
  rateLimit: { action: "rate_limit", aggregateBy: ["ip"], windowSize: 60_000, windowLimit: 20 },
};
