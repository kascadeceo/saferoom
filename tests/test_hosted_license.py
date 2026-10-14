"""Hosted license layer: Python CLI crypto and the Node (Netlify function)
crypto must agree byte-for-byte. These tests mint on one side and verify on
the other, in both directions, plus rejection cases. Skips if node is absent.
"""
import datetime
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

CLI = Path(__file__).resolve().parents[1] / 'saferoom.py'
LIB = Path(__file__).resolve().parents[1] / 'netlify' / 'functions' / 'lib' / 'license.mjs'

spec = importlib.util.spec_from_file_location('sr_cli', CLI)
sr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sr)


def node(args):
    argv = ['node', str(LIB), *args]
    return subprocess.run([a for a in argv if a is not None],
                          capture_output=True, text=True, check=True).stdout.strip()


def python_mint(payload, secret):
    body = sr.b64u_encode(json.dumps(payload, sort_keys=True,
                                     separators=(',', ':')).encode())
    sig = sr.b64u_encode(sr.hmac.new(secret, body.encode('ascii'),
                                     sr.hashlib.sha256).digest())
    return f'SR-{body}-{sig}'


class HostedLicenseCrossCheck(unittest.TestCase):
    TIER_PAYLOAD = {'v': 1, 'tier': 'pro-mo', 'email': 'buyer@example.com',
                    'exp': '2027-01-31'}

    @classmethod
    def setUpClass(cls):
        if not shutil.which('node'):
            raise unittest.SkipTest('node not installed')

    def test_mint_is_byte_identical_across_languages(self):
        node_key = node(['mint', 'secret-test', json.dumps(self.TIER_PAYLOAD)])
        self.assertEqual(node_key, python_mint(self.TIER_PAYLOAD, b'secret-test'))

    def test_node_verifies_python_minted_paid_key(self):
        key = python_mint(self.TIER_PAYLOAD, b'secret-test')
        out = json.loads(node(['verify', 'secret-test', key, '2026-10-14']))
        self.assertTrue(out['valid'], out)
        self.assertEqual(out['payload']['tier'], 'pro-mo')

    def test_python_verifies_node_minted_paid_key(self):
        key = node(['mint', 'secret-test', json.dumps(self.TIER_PAYLOAD)])
        payload, body, sig = sr.decode_license(key)
        self.assertTrue(sr.license_sig_ok(body, sig, b'secret-test'))
        self.assertEqual(payload['email'], 'buyer@example.com')

    def test_hosted_verify_round_trip_via_cli_constants(self):
        # The real hosted secret stays on the server; this proves the wire
        # format the CLI POSTs is exactly what the Node endpoint parses.
        key = node(['mint', sr.EVAL_SECRET.decode(), json.dumps(
            {'v': 1, 'tier': 'eval', 'email': 'e@x.co', 'exp': '2027-01-01'})])
        out = json.loads(node(['verify', sr.EVAL_SECRET.decode(), key, '2026-10-14']))
        self.assertTrue(out['valid'], out)
        # ...and the CLI's own eval path accepts the same key.
        payload, body, sig = sr.decode_license(key)
        self.assertTrue(sr.license_sig_ok(body, sig, sr.EVAL_SECRET))

    def test_node_rejects_tampered_payload(self):
        key = python_mint(self.TIER_PAYLOAD, b'secret-test')
        body, sig = key[3:].rsplit('-', 1)
        # A flipped body char breaks decode or signature; a flipped sig char
        # must surface as a signature mismatch specifically.
        evil_body = ('B' if body[0] != 'B' else 'C') + body[1:]
        out = json.loads(node(['verify', 'secret-test', f'SR-{evil_body}-{sig}']))
        self.assertFalse(out['valid'])
        self.assertIn(out['reason'], ('payload', 'signature'))
        evil_sig = sig[:-1] + ('A' if sig[-1] != 'A' else 'B')
        out = json.loads(node(['verify', 'secret-test', f'SR-{body}-{evil_sig}']))
        self.assertFalse(out['valid'])
        self.assertEqual(out['reason'], 'signature')

    def test_node_rejects_expired_key(self):
        payload = {'v': 1, 'tier': 'pro-yr', 'email': 'x@y.co',
                   'exp': '2026-10-13'}
        key = python_mint(payload, b'secret-test')
        out = json.loads(node(['verify', 'secret-test', key, '2026-10-14']))
        self.assertFalse(out['valid'])
        self.assertEqual(out['reason'], 'expired')

    def test_node_rejects_wrong_secret_and_garbage(self):
        key = python_mint(self.TIER_PAYLOAD, b'secret-test')
        out = json.loads(node(['verify', 'other-secret', key, None]))
        self.assertFalse(out['valid'])
        for junk in ('', 'SR-bogus', 'not-even-close',
                     python_mint(self.TIER_PAYLOAD, b'secret-test')[:-2]):
            self.assertFalse(json.loads(
                node(['verify', 'secret-test', junk]))['valid'], junk)

    def test_lifetime_payload_has_no_exp(self):
        payload = {'v': 1, 'tier': 'lifetime', 'email': 'l@example.com'}
        node_key = node(['mint', 'secret-test', json.dumps(payload)])
        self.assertEqual(node_key, python_mint(payload, b'secret-test'))
        decoded, _, _ = sr.decode_license(node_key)
        self.assertNotIn('exp', decoded)


if __name__ == '__main__':
    unittest.main()
