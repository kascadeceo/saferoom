// GET /welcome?session_id=cs_live_... — the Stripe Payment Link success page.
// Verifies the checkout session directly against the Stripe API, derives the
// buyer's license key deterministically from the paid session, and shows it
// with activation instructions.
//
// Stateless: the key is a pure function of (session payment data,
// LICENSE_SECRET), so revisiting the same success URL always re-derives the
// same key — subscriptions get a fresh `exp` each billing period, which IS
// the renewal flow ("bookmark this page"). No database, nothing to lose.
//
// Required env vars (founder sets once in Netlify — see docs/founder-runbook.md):
//   STRIPE_SECRET_KEY
//   LICENSE_SECRET
//   PAYMENT_LINK_PRO_MO  (plink_... -> tier "pro-mo")
//   PAYMENT_LINK_PRO_YR  (plink_... -> tier "pro-yr")
//   PAYMENT_LINK_LIFETIME(plink_... -> tier "lifetime")
import type { Config } from "@netlify/functions";
import { mintLicense } from "./lib/license.mjs";

declare const Netlify: { env: { get(name: string): string | undefined } };

interface StripeSubscription {
  current_period_end?: number;
}

interface StripeSession {
  payment_status?: string;
  mode?: string;
  payment_link?: string;
  customer_email?: string | null;
  customer_details?: { email?: string | null } | null;
  subscription?: StripeSubscription | string | null;
}

function env(name: string): string | undefined {
  return Netlify.env.get(name) || process.env[name];
}

function page(title: string, bodyHtml: string, ok = true): Response {
  const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>${title} — SafeRoom</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box;margin:0}
body{font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:#0d1117;color:#e6edf3;display:flex;min-height:100vh;align-items:center;justify-content:center;padding:1.5rem}
.card{max-width:640px;width:100%;background:#161b22;border:1px solid #30363d;border-radius:12px;padding:2.5rem}
h1{font-size:1.6rem;margin-bottom:.75rem}
h1 .ok{color:#3fb950}h1 .bad{color:#f85149}
p{margin:.6rem 0;color:#c9d1d9}
.key{display:block;font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;background:#0d1117;
  border:1px solid #30363d;border-radius:8px;padding:1rem;margin:1rem 0;word-break:break-all;user-select:all}
button{background:#238636;border:1px solid #2ea043;color:#fff;border-radius:6px;padding:.5rem 1.1rem;
  font-weight:600;cursor:pointer}
button:hover{background:#2ea043}
code{background:#0d1117;border:1px solid #30363d;border-radius:4px;padding:.1rem .35rem;
  font-family:ui-monospace,Menlo,monospace;font-size:.9em}
.note{font-size:.85rem;color:#8b949e;margin-top:1.25rem;border-top:1px solid #21262d;padding-top:1rem}
a{color:#58a6ff}
</style></head><body><div class="card">${bodyHtml}
<p class="note">SafeRoom — the sign-off layer for AI coding agents · <a href="/">getsaferoom.netlify.app</a></p>
</div>
</body></html>`;
  return new Response(html, {
    status: 200,
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
}

function tierForPaymentLink(linkId?: string): string | null {
  if (!linkId) return null;
  const map: Record<string, string> = {
    [env("PAYMENT_LINK_PRO_MO") ?? ""]: "pro-mo",
    [env("PAYMENT_LINK_PRO_YR") ?? ""]: "pro-yr",
    [env("PAYMENT_LINK_LIFETIME") ?? ""]: "lifetime",
  };
  return map[linkId] ?? null;
}

function ymd(unixSeconds: number): string {
  return new Date(unixSeconds * 1000).toISOString().slice(0, 10);
}

export default async (req: Request) => {
  const sessionId = new URL(req.url).searchParams.get("session_id")?.trim();
  if (!sessionId || !/^cs_(test|live)_[A-Za-z0-9]+$/.test(sessionId)) {
    return page("Almost there",
      `<h1 class="bad">Missing checkout session</h1>
      <p>This page needs a Stripe <code>session_id</code>. Use the link from your purchase receipt.</p>`);
  }
  const stripeKey = env("STRIPE_SECRET_KEY");
  const licenseSecret = env("LICENSE_SECRET");
  if (!stripeKey || !licenseSecret) {
    return page("Hold on",
      `<h1 class="bad">Store not fully configured</h1>
      <p>Payment verified, but licensing isn't configured yet. Email your receipt and we'll send your key manually.</p>`);
  }

  let session: StripeSession;
  try {
    const url = `https://api.stripe.com/v1/checkout/sessions/${encodeURIComponent(sessionId)}?expand[]=subscription`;
    const res = await fetch(url, { headers: { Authorization: `Bearer ${stripeKey}` } });
    if (!res.ok) throw new Error(`stripe ${res.status}`);
    session = await res.json() as StripeSession;
  } catch {
    return page("Hmm",
      `<h1 class="bad">We couldn't verify that checkout session</h1>
      <p>The link may be mistyped or expired. Use the exact URL from your purchase receipt.</p>`);
  }

  if (session.payment_status !== "paid") {
    return page("Payment pending",
      `<h1>Payment not confirmed yet</h1>
      <p>If you just paid, refresh in a moment. If you believe this is wrong, reply to your receipt email.</p>`);
  }

  const tier = tierForPaymentLink(session.payment_link);
  if (!tier) {
    return page("Almost there",
      `<h1 class="bad">Unknown product</h1>
      <p>Your purchase didn't match a SafeRoom tier. Reply to your receipt email and we'll fix it fast.</p>`);
  }

  const email = session.customer_details?.email || session.customer_email || "seat";
  const payload: { v: 1; tier: string; email: string; exp?: string } = { v: 1, tier, email };
  if (tier !== "lifetime") {
    const sub = typeof session.subscription === "object" ? session.subscription : null;
    const periodEnd = sub?.current_period_end;
    if (!periodEnd) {
      return page("Almost there",
        `<h1 class="bad">Subscription not readable</h1>
        <p>Payment received, but we couldn't read the billing period. Reply to your receipt email for your key.</p>`);
    }
    payload.exp = ymd(periodEnd);
  }

  let key: string;
  try {
    key = await mintLicense(licenseSecret, payload);
  } catch {
    return page("Almost there",
      `<h1 class="bad">We couldn't mint your license</h1>
      <p>This is on us — reply to your receipt email and we'll send your key manually.</p>`);
  }

  const expires = payload.exp
    ? `expires <strong>${payload.exp}</strong> (renews with your billing cycle — revisit this page and re-activate)`
    : `valid <strong>for the life of the product</strong>`;
  const tierName = tier === "lifetime" ? "Pro — Lifetime" : "SafeRoom Pro";
  return page("Welcome to SafeRoom Pro", `
  <h1 class="ok">You're in. ${tierName}</h1>
  <p>Seat: <strong>${email}</strong> · ${expires}</p>
  <p>Your license key (click to select, then copy):</p>
  <code class="key" id="k">${key}</code>
  <button onclick="navigator.clipboard.writeText(document.getElementById('k').textContent).then(this.textContent='Copied ✓')">Copy key</button>
  <p style="margin-top:1.25rem">Activate in your repo:</p>
  <p><code>saferoom license activate ${key.slice(0, 18)}…</code> — paste the full key after <code>activate</code>.</p>
  <div class="note" style="border-top:none;padding-top:.25rem">
  <strong>Bookmark this page.</strong> It re-derives your key from your Stripe purchase — it will
  still work after a machine change, and for monthly/yearly plans it carries each renewal's new
  expiry date. Lost the bookmark? Your purchase receipt email has the link.</div>`);
};

export const config: Config = {
  path: "/welcome",
  method: ["GET"],
};
