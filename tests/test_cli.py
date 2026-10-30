from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def test_help_runs_as_module_without_touching_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, 'HERMES_HOME':temp}
            result = subprocess.run([sys.executable,'-m','hermes_bilibili','--help'], cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('status', result.stdout)
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_offline_status_uses_isolated_hermes_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, 'HERMES_HOME':temp}
            result = subprocess.run([sys.executable,'-m','hermes_bilibili','status'], cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            import json
            output = json.loads(result.stdout)
            self.assertTrue(output['success'])
            self.assertFalse(output['data']['writes_enabled'])
            self.assertFalse((Path(temp)/'config.yaml').exists())
            self.assertTrue((Path(temp)/'plugin-data'/'hermes-bilibili'/'state.db').exists())

    def test_local_installer_copies_only_release_files_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, 'HERMES_HOME':temp}
            command = [sys.executable, str(ROOT/'scripts'/'install_local.py')]
            first = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(first.returncode,0,first.stdout+first.stderr)
            target = Path(temp)/'plugins'/'hermes-bilibili'
            self.assertTrue((target/'plugin.yaml').exists())
            self.assertFalse((target/'tests').exists())
            self.assertFalse((Path(temp)/'config.yaml').exists())
            second = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
            self.assertNotEqual(second.returncode,0)
            self.assertIn('already exists', second.stderr)


if __name__ == '__main__':
    unittest.main()
