"""Hosted function handlers (api-license-verify.mts, welcome.mts) exercised
end-to-end with a mocked Netlify env and a mocked Stripe API — no network,
no Netlify runtime required. Node >= 22.18 (native .mts type stripping).
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
FUNCS = REPO / 'netlify' / 'functions'

DRIVER = r'''
import verifyFn from "__VERIFY__";
import welcomeFn from "__WELCOME__";
import { mintLicense } from "__LIB__";

const SECRET = "test-license-secret";
const KEYS = {
  LICENSE_SECRET: SECRET,
  STRIPE_SECRET_KEY: "sk_test_42",
  PAYMENT_LINK_PRO_MO: "plink_mo001",
  PAYMENT_LINK_PRO_YR: "plink_yr001",
  PAYMENT_LINK_LIFETIME: "plink_life001",
};
globalThis.Netlify = { env: { get: (n) => KEYS[n] } };

const results = [];
const check = (name, ok, detail = "") => results.push({ name, ok, detail });
const html = (r) => r.text();

// --- /api/license/verify --------------------------------------------------
const goodKey = await mintLicense(SECRET, { v: 1, tier: "pro-mo",
  email: "buyer@example.com", exp: "2099-01-01" });

let r = await verifyFn(new Request("https://x/api/license/verify",
  { method: "POST", body: JSON.stringify({ key: goodKey }) }));
let j = await r.json();
check("verify accepts paid key", j.valid === true && j.tier === "pro-mo", JSON.stringify(j));

r = await verifyFn(new Request("https://x/api/license/verify",
  { method: "POST", body: JSON.stringify({ key: "SR-garbage" }) }));
j = await r.json();
check("verify rejects garbage", j.valid === false, JSON.stringify(j));

r = await verifyFn(new Request("https://x/api/license/verify", { method: "GET" }));
check("verify rejects GET", r.status === 405, String(r.status));

// --- /welcome --------------------------------------------------------------
let stripeSession = null;
const stripeCalls = [];
const realFetch = globalThis.fetch;
globalThis.fetch = async (url, opts) => {
  stripeCalls.push(String(url));
  if (!String(url).startsWith("https://api.stripe.com/v1/checkout/sessions/")) {
    return realFetch(url, opts);
  }
  if (stripeSession === null) {
    return new Response(JSON.stringify({ error: { message: "no such session" } }),
      { status: 404 });
  }
  return new Response(JSON.stringify(stripeSession), { status: 200 });
};

const welcome = async (qs) =>
  welcomeFn(new Request(`https://x/welcome?${qs}`));

// missing session id
r = await welcome("");
check("welcome without session_id shows help", (await html(r)).includes("Missing checkout session"));

// unknown session (stripe 404)
stripeSession = null;
r = await welcome("session_id=cs_test_nope");
check("welcome on bad session errors politely", (await html(r)).includes("couldn't verify"));

// unpaid session
stripeSession = { payment_status: "unpaid", payment_link: "plink_mo001" };
r = await welcome("session_id=cs_test_1");
check("welcome on unpaid shows pending", (await html(r)).includes("Payment not confirmed"));

// paid monthly subscription
const periodEnd = Math.floor(Date.now() / 1000) + 31 * 86400;
stripeSession = { payment_status: "paid", payment_link: "plink_mo001",
  customer_details: { email: "buyer@example.com" },
  subscription: { current_period_end: periodEnd } };
r = await welcome("session_id=cs_test_1");
let body = await html(r);
let m = body.match(/SR-[A-Za-z0-9_-]{10,}-[A-Za-z0-9_-]{43}/);
check("welcome mints visible key", m !== null, body.slice(0, 200));
if (m) {
  const { verifyLicense } = await import("__LIB__");
  const v = await verifyLicense(m[0], SECRET, null);
  const wantExp = new Date(periodEnd * 1000).toISOString().slice(0, 10);
  check("welcome key verifies + carries expiry",
    v.valid === true && v.payload.tier === "pro-mo" && v.payload.exp === wantExp,
    JSON.stringify(v));
  check("welcome shows activation command", body.includes("license activate"));
  // idempotent: same session -> same key
  const body2 = await html(await welcome("session_id=cs_test_1"));
  check("welcome is idempotent per session", body2.includes(m[0]));
}

// lifetime purchase (mode=payment, no subscription)
stripeSession = { payment_status: "paid", payment_link: "plink_life001",
  customer_details: { email: "lifer@example.com" } };
body = await html(await welcome("session_id=cs_test_life"));
m = body.match(/SR-[A-Za-z0-9_-]{10,}-[A-Za-z0-9_-]{43}/);
if (m) {
  const { verifyLicense } = await import("__LIB__");
  const v = await verifyLicense(m[0], SECRET, null);
  check("lifetime key has no expiry",
    v.valid === true && v.payload.tier === "lifetime" && !v.payload.exp,
    JSON.stringify(v.payload));
} else {
  check("lifetime key minted", false, body.slice(0, 200));
}

// unknown payment link
stripeSession = { payment_status: "paid", payment_link: "plink_evil",
  customer_details: { email: "x@y.co" } };
r = await welcome("session_id=cs_test_2");
check("welcome rejects unknown product", (await html(r)).includes("Unknown product"));

// unconfigured store
KEYS.STRIPE_SECRET_KEY = undefined;
r = await welcome("session_id=cs_test_3");
check("welcome degrades when unconfigured", (await html(r)).includes("not fully configured"));
KEYS.STRIPE_SECRET_KEY = "sk_test_42";

// never leaks secrets into logs/headers
check("no secret in any response", !JSON.stringify(results).includes(SECRET)
  && !stripeCalls.some(u => u.includes("sk_test")));
console.log(JSON.stringify(results));
'''


class HostedFunctionHandlers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('node'):
            raise unittest.SkipTest('node not installed')

    def test_handlers(self):
        driver = (DRIVER
                  .replace('__VERIFY__', str(FUNCS / 'api-license-verify.mts'))
                  .replace('__WELCOME__', str(FUNCS / 'welcome.mts'))
                  .replace('__LIB__', str(FUNCS / 'lib' / 'license.mjs')))
        with tempfile.TemporaryDirectory(prefix='saferoom-hosted-') as tmp:
            path = Path(tmp) / 'driver.mjs'
            path.write_text(driver)
            proc = subprocess.run(['node', str(path)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        results = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertGreaterEqual(len(results), 12, results)
        for r in results:
            self.assertTrue(r['ok'], f"{r['name']}: {r['detail']}")


if __name__ == '__main__':
    unittest.main()
