"""Pro features: eval license, HTML report, compliance evidence chain."""
import glob
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CLI = Path(__file__).resolve().parents[1] / 'saferoom.py'
ENV = {**os.environ, 'GIT_AUTHOR_NAME': 'Fixture', 'GIT_COMMITTER_NAME': 'Fixture',
       'GIT_AUTHOR_EMAIL': 'fixture@example.com', 'GIT_COMMITTER_EMAIL': 'fixture@example.com',
       'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}

spec = importlib.util.spec_from_file_location('sr_cli', CLI)
sr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sr)


class ProFeatures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='saferoom-pro-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'repo'
        self.root.mkdir()
        self.run_cmd(['git', 'init', '-q', '-b', 'main'], cwd=self.root)
        self.run_cmd(['git', 'config', 'user.name', 'Fixture'], cwd=self.root)
        self.run_cmd(['git', 'config', 'user.email', 'fixture@example.com'], cwd=self.root)
        (self.root / '.gitignore').write_text('.saferoom/\n')
        (self.root / 'app.py').write_text('print("v1")\n')
        (self.root / '.env').write_text('API_KEY=real-secret\n')
        self.run_cmd(['git', 'add', '-A'], cwd=self.root)
        self.run_cmd(['git', 'commit', '-qm', 'init'], cwd=self.root)
        self.sr('init')
        self.sr('run', '--local', '--', 'echo v2 > app2.txt && cat .env')

    def run_cmd(self, cmd, check=True, **kw):
        r = subprocess.run(cmd, cwd=kw.pop('cwd', self.root), env=ENV, text=True,
                           capture_output=True, **kw)
        if check:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def sr(self, *args, check=True):
        return self.run_cmd([sys.executable, str(CLI), *args], check=check)

    def activate_eval(self):
        key = self.sr('license', 'eval', '--email', 'qa@team').stdout.strip()
        self.assertRegex(key, r'^SR-[A-Za-z0-9_-]+-[A-Za-z0-9_-]{43}$')
        self.sr('license', 'activate', key)
        return key

    def session_glob(self, name):
        matches = sorted(glob.glob(str(self.root / '.saferoom' / 'sessions' / '*' / name)))
        self.assertTrue(matches, f'no {name} produced')
        return matches[-1]

    def test_eval_license_roundtrip_and_status(self):
        self.activate_eval()
        status = self.sr('license', 'status').stdout
        self.assertIn('tier=eval', status)
        self.assertIn('hosted_verified=True', status)

    def test_bad_key_rejected(self):
        r = self.sr('license', 'activate', 'SR-not-a-real-key-' + 'x' * 43, check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn('license', r.stdout)

    def test_tampered_eval_signature_rejected(self):
        key = self.activate_eval()
        r = self.sr('license', 'activate', key[:-1] + ('A' if key[-1] != 'A' else 'B'), check=False)
        self.assertEqual(r.returncode, 1)

    def test_pro_features_gated_without_license(self):
        r = self.sr('export', '--compliance', check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn('Pro feature', r.stdout)
        r = self.sr('review', '--html', check=False)
        self.assertEqual(r.returncode, 1)

    def test_expired_license_blocks_pro(self):
        key = sr.mint_eval_license('qa@team', 1)
        payload, body, _ = sr.decode_license(key)
        payload['exp'] = '2020-01-01'
        stale_body = sr.b64u_encode(json.dumps(payload, sort_keys=True,
                                               separators=(',', ':')).encode())
        stale_sig = sr.b64u_encode(__import__('hmac').new(sr.EVAL_SECRET, stale_body.encode(),
                                                          __import__('hashlib').sha256).digest())
        stale_key = f'SR-{stale_body}-{stale_sig}'
        self.sr('license', 'activate', stale_key)
        r = self.sr('export', '--compliance', check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn('expired', r.stdout)

    def test_html_report_self_contained(self):
        self.activate_eval()
        self.sr('review', '--html')
        page = Path(self.session_glob('report.html')).read_text()
        self.assertIn('app2.txt', page)
        self.assertIn('Evaluation copy', page)
        self.assertNotRegex(page, r'<(script|link)[ >]')
        self.assertIn('<style>', page)

    def test_compliance_export_chain(self):
        self.activate_eval()
        self.sr('approve')
        self.sr('export', '--compliance')
        evidence = Path(self.session_glob('evidence.jsonl')).read_text().splitlines()
        events = [json.loads(line) for line in evidence]
        kinds = {r['event'] for r in events}
        self.assertTrue({'run', 'command', 'diff', 'decision', 'export'} <= kinds)
        for rec in events:
            self.assertTrue(all(k in rec for k in ('ts', 'actor', 'event', 'sha256', 'prev_sha256')))
        self.sr('export', '--verify')

    def test_tampered_evidence_detected(self):
        self.activate_eval()
        self.sr('export', '--compliance')
        target = Path(self.session_glob('evidence.jsonl'))
        lines = target.read_text().splitlines()
        rec = json.loads(lines[2])
        rec['detail']['forged'] = True
        lines[2] = json.dumps(rec, sort_keys=True, separators=(',', ':'))
        target.write_text('\n'.join(lines) + '\n')
        r = self.sr('export', '--verify', check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn('broken', r.stdout)


if __name__ == '__main__':
    unittest.main()
