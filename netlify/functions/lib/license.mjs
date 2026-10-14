// SafeRoom hosted license crypto — the JavaScript mirror of the license code
// in saferoom.py. The two implementations MUST agree byte-for-byte:
//
//   key   = "SR-" + b64url(payload_json) + "-" + b64url(HMAC-SHA256(secret, b64url_body))
//   json  = keys sorted (email, exp, tier, v), separators (",", ":"), ASCII
//   b64url = standard base64 with "+"->"-", "/"->"_", padding stripped
//
// Pure module: no Netlify APIs, no network, no storage. The /welcome and
// /api/license/verify function handlers live in sibling files; this file is
// also runnable as a tiny CLI so cross-language tests can drive it:
//
//   node license.mjs mint    <secret> '<payload-json>'          -> prints key
//   node license.mjs verify  <secret> <key> [YYYY-MM-DD]        -> prints {valid,...}

const TIERS = ["eval", "pro-mo", "pro-yr", "lifetime"];
const KEY_RE = /^SR-([A-Za-z0-9_-]+)-([A-Za-z0-9_-]{43})$/;
const encoder = new TextEncoder();

function b64uEncode(bytes) {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64uDecode(text) {
  const pad = (4 - (text.length % 4)) % 4;
  const b64 = text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat(pad);
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

async function hmacSha256(secretStr, msgStr) {
  const key = await crypto.subtle.importKey(
    "raw", encoder.encode(secretStr), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, encoder.encode(msgStr)));
}

function constantTimeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

// Python: json.dumps(payload, sort_keys=True, separators=(",", ":"))
// Emails are expected to be ASCII; non-ASCII would need Python's
// ensure_ascii escaping, which we reject here rather than diverge silently.
export function canonicalPayload(payload) {
  const parts = [];
  for (const k of ["email", "exp", "tier", "v"]) {
    if (payload[k] === undefined || payload[k] === null) continue;
    // "v" stays a JSON number (Python: 1); other fields are strings.
    const val = k === "v" ? payload[k] : String(payload[k]);
    if (typeof val === "string") {
      for (const ch of val) {
        if (ch.charCodeAt(0) > 126) throw new Error("non-ASCII license field value");
      }
    }
    parts.push(`"${k}":${JSON.stringify(val)}`);
  }
  return "{" + parts.join(",") + "}";
}

export async function mintLicense(secret, payload) {
  if (payload.v !== 1) throw new Error("unsupported payload version");
  if (!TIERS.includes(payload.tier)) throw new Error("unsupported tier");
  const body = b64uEncode(encoder.encode(canonicalPayload(payload)));
  const sig = b64uEncode(await hmacSha256(secret, body));
  return `SR-${body}-${sig}`;
}

// Returns { valid, payload?, reason? }. todayStr is "YYYY-MM-DD" (UTC) or
// null to skip the expiry check.
export async function verifyLicense(key, secret, todayStr) {
  const m = KEY_RE.exec(String(key ?? "").trim());
  if (!m) return { valid: false, reason: "shape" };
  const body = m[1], sig = m[2];
  let payload;
  try {
    payload = JSON.parse(new TextDecoder().decode(b64uDecode(body)));
  } catch {
    return { valid: false, reason: "payload" };
  }
  if (typeof payload !== "object" || payload === null) return { valid: false, reason: "payload" };
  if (payload.v !== 1 || !TIERS.includes(payload.tier)) return { valid: false, reason: "payload" };
  const want = b64uEncode(await hmacSha256(secret, body));
  if (!constantTimeEqual(want, sig)) return { valid: false, reason: "signature" };
  if (payload.exp && todayStr && String(payload.exp) < todayStr) {
    return { valid: false, reason: "expired" };
  }
  return { valid: true, payload };
}

// ---- tiny CLI harness (used by tests/test_hosted_license.py) --------------
const isMain = process.argv[1] &&
  import.meta.url === new URL("file://" + process.argv[1]).href;
if (isMain) {
  const [, , cmd, secret, arg, today] = process.argv;
  try {
    if (cmd === "mint" && secret && arg !== undefined) {
      console.log(await mintLicense(secret, JSON.parse(arg)));
    } else if (cmd === "verify" && secret && arg !== undefined) {
      const r = await verifyLicense(arg, secret, today ?? null);
      console.log(JSON.stringify(r));
    } else {
      console.error("usage: license.mjs mint <secret> <payload-json> | verify <secret> <key> [today]");
      process.exit(2);
    }
  } catch (err) {
    console.error(String(err && err.message ? err.message : err));
    process.exit(1);
  }
}
