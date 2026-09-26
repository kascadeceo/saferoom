"""Real pre-push checks against disposable LOCAL remotes; no hosted pushes."""
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


class PushGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='saferoom-push-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'repo'
        self.root.mkdir()
        self.remote = Path(self.tmp.name) / 'remote.git'
        self.git('init', '-q', '--bare', str(self.remote))
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.com')
        (self.root / '.gitignore').write_text('.saferoom/\n')
        (self.root / 'app.txt').write_text('before\n')
        self.commit('initial')
        self.git('remote', 'add', 'origin', str(self.remote))
        self.git('push', '-q', 'origin', 'main')
        self.sr('gate', '--install-hook', '--trusted-base', 'HEAD')
        self.session = self.root / '.saferoom/sessions/fixture'
        self.session.mkdir(parents=True)
        (self.root / 'app.txt').write_text('after\n')
        self.patch = self.git('diff', '--binary').stdout
        (self.session / 'changes.patch').write_text(self.patch)
        (self.session / 'report.json').write_text(json.dumps({'files_changed':['app.txt'], 'exit_code':0}))
        (self.session / 'report.md').write_text('Synthetic review.\n')
        self.git('restore', 'app.txt')

    def run_cmd(self, cmd, check=True, **kw):
        r = subprocess.run(cmd, cwd=self.root, env=ENV, text=True, capture_output=True, **kw)
        if check:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def git(self, *args, **kw):
        return self.run_cmd(['git', *args], **kw)

    def sr(self, *args, **kw):
        return self.run_cmd([sys.executable, '-B', str(CLI), *args], **kw)

    def commit(self, label):
        self.git('add', '-A')
        self.git('commit', '-qm', label)

    def push(self, allowed):
        before = self.git('--git-dir='+str(self.remote), 'rev-parse', 'refs/heads/main').stdout
        result = self.git('push', 'origin', 'main', check=False)
        self.assertEqual(result.returncode == 0, allowed, result.stdout + result.stderr)
        after = self.git('--git-dir='+str(self.remote), 'rev-parse', 'refs/heads/main').stdout
        self.assertEqual(after, self.git('rev-parse', 'HEAD').stdout if allowed else before)

    def manual_change(self):
        (self.root / 'app.txt').write_text('after\n')
        self.commit('unreviewed')

    def test_pending_blocks(self):
        self.manual_change()
        self.push(False)

    def test_approved_exact_tree_allows(self):
        self.sr('approve', 'fixture')
        self.commit('approved')
        self.push(True)

    def test_rejected_and_dismissed_never_authorize_shipping(self):
        for mode in ['--reject', '--dismiss']:
            self.sr('gate', mode, 'fixture', '--reason', 'not wanted')
            self.assertEqual((self.root / 'app.txt').read_text(), 'before\n')
            self.sr('gate')
        self.manual_change()
        self.push(False)

    def test_rejected_push_blocks(self):
        self.sr('gate','--reject','fixture')
        self.manual_change(); self.push(False)

    def test_nested_env_not_applied_or_approved(self):
        (self.root/'nested').mkdir()
        (self.root/'nested/.env').write_text('SYNTHETIC=before\n')
        self.commit('baseline addition')
        # Explicitly approved history must include this earlier commit too; inspect application only here.
        (self.root/'nested/.env').write_text('SYNTHETIC=after\n')
        (self.root/'app.txt').write_text('after\n')
        (self.session/'changes.patch').write_text(self.git('diff').stdout)
        self.git('restore','.')
        self.sr('approve','fixture')
        self.assertEqual((self.root/'nested/.env').read_text(),'SYNTHETIC=before\n')
        self.assertEqual((self.root/'app.txt').read_text(),'after\n')

    def test_unknown_blocks(self):
        self.sr('approve', 'fixture')
        f = self.session / 'approvals.jsonl'
        r = json.loads(f.read_text()); r['decision'] = 'anything'
        f.write_text(json.dumps(r)+'\n')
        self.commit('unknown')
        self.push(False)

    def test_stale_hash_blocks(self):
        self.sr('approve', 'fixture')
        (self.session / 'changes.patch').write_text(self.patch+'\n')
        self.commit('stale')
        self.push(False)

    def test_extra_file_in_approved_commit_blocks(self):
        self.sr('approve', 'fixture')
        (self.root / 'unreviewed.txt').write_text('extra')
        self.commit('extra')
        self.push(False)

    def test_approved_tip_cannot_hide_unreviewed_intermediate_commit(self):
        self.sr('approve', 'fixture')
        (self.root / 'extra.txt').write_text('extra')
        self.commit('extra history')
        (self.root / 'extra.txt').unlink()
        self.commit('same final tree')
        self.push(False)

    def test_later_unapproved_commit_blocks(self):
        self.sr('approve', 'fixture'); self.commit('approved')
        (self.root / 'later.txt').write_text('later'); self.commit('later')
        self.push(False)

    def test_rejection_does_not_block_separately_approved_work(self):
        self.sr('gate', '--dismiss', 'fixture')
        other=self.root / '.saferoom/sessions/other'; other.mkdir()
        (other/'changes.patch').write_text(self.patch)
        self.sr('approve','other'); self.commit('separate explicit approval')
        self.push(True)

    def test_malformed_record_and_legacy_unbound_approval_block(self):
        self.manual_change()
        for value in ['{', '[]', json.dumps({'decision':'approved'})]:
            (self.session/'approvals.jsonl').write_text(value)
            self.push(False)

    def test_no_session_manual_commit_still_blocks(self):
        (self.session/'changes.patch').write_text('')
        self.manual_change(); self.push(False)

    def test_new_branch_is_checked(self):
        self.sr('approve','fixture'); self.commit('approved')
        self.git('push','origin','HEAD:refs/heads/new-approved')
        (self.root/'extra').write_text('extra'); self.commit('unapproved')
        r=self.git('push','origin','HEAD:refs/heads/new-unapproved',check=False)
        self.assertNotEqual(r.returncode,0)

    def test_no_verify_is_documented_bypass(self):
        self.manual_change()
        self.git('push','--no-verify','origin','main')

    def test_installer_refuses_overwrite_and_dismissal_of_applied_approval(self):
        self.assertNotEqual(self.sr('gate','--install-hook','--trusted-base','HEAD',check=False).returncode,0)
        self.sr('approve','fixture')
        self.assertNotEqual(self.sr('gate','--dismiss','fixture',check=False).returncode,0)

    def test_tags_deletions_and_multiple_ref_updates(self):
        self.sr('approve','fixture'); self.commit('approved')
        self.git('tag','demo-tag')
        self.assertNotEqual(self.git('push','origin','demo-tag',check=False).returncode,0)
        self.assertNotEqual(self.git('push','origin',':refs/heads/main',check=False).returncode,0)
        self.git('branch','approved-branch')
        (self.root/'extra.txt').write_text('extra'); self.commit('extra')
        self.assertNotEqual(self.git('push','origin','main','approved-branch',check=False).returncode,0)
        self.assertNotEqual(self.git('--git-dir='+str(self.remote),'show-ref','--verify','refs/heads/approved-branch',check=False).returncode,0)

    def test_failed_execution_is_not_a_passed_check(self):
        self.sr('run','--local','--','exit 7')
        rows=json.loads(self.sr('sessions','--json').stdout)
        self.assertTrue(any(r['checks']=='command_failed' for r in rows))

    def test_subdirectory_cannot_bypass(self):
        self.manual_change()
        sub=self.root/'nested'; sub.mkdir()
        r=subprocess.run([sys.executable,'-B',str(CLI),'gate'],cwd=sub,env=ENV,capture_output=True)
        self.assertNotEqual(r.returncode,0)


if __name__ == '__main__':
    unittest.main()
