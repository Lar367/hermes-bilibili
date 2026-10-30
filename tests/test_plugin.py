import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FakeContext:
    def __init__(self):
        self.tools = {}
        self.hooks = {}
        self.commands = {}
        self.cli = {}
        self.skills = {}

    def register_tool(self, **kw):
        self.tools[kw['name']] = kw

    def register_hook(self, name, handler):
        self.hooks[name] = handler

    def register_command(self, name, handler, **kw):
        self.commands[name] = handler

    def register_cli_command(self, name, **kw):
        self.cli[name] = kw

    def register_skill(self, name, path):
        self.skills[name] = path


def load_plugin():
    path = ROOT / '__init__.py'
    if not path.exists():
        raise AssertionError('native Hermes plugin entry point is not implemented')
    spec = importlib.util.spec_from_file_location('tested_bili_plugin', path, submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PackagingTests(unittest.TestCase):
    def test_yaml_is_declared_only_as_a_test_extra(self):
        import tomllib

        metadata = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
        self.assertEqual(metadata['dependencies'], [])
        self.assertIn('PyYAML>=6.0,<7', metadata.get('optional-dependencies', {}).get('test', []))


class HostVerificationHelperTests(unittest.TestCase):
    def test_probe_accepts_public_cli_enabled_status_shape(self):
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('host_probe_test', ROOT / 'scripts' / 'verify_host.py')
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as scratch:
            home = Path(scratch) / 'profile'
            home.mkdir()
            result = subprocess.CompletedProcess([], 0, json.dumps([{'name': 'hermes-bilibili', 'status': 'enabled'}]), '')
            with patch.dict(os.environ, {'HOME': str(home), 'HERMES_HOME': str(home), 'TMPDIR': scratch}), patch('subprocess.run', return_value=result):
                self.assertEqual(module.main(), 0)

    def test_probe_refuses_non_scratch_home_before_loading_host(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / 'profile'
            home.mkdir()
            config = home / 'config.yaml'
            config.write_text('sentinel: unchanged\n')
            result = subprocess.run(
                [sys.executable, str(ROOT / 'scripts' / 'verify_host.py')],
                env={**os.environ, 'HOME': str(home), 'HERMES_HOME': str(home),
                     'TMPDIR': str(home / 'not-a-scratch-directory')},
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn('refusing non-scratch HERMES_HOME', result.stderr)
            self.assertEqual(config.read_text(), 'sentinel: unchanged\n')
            self.assertEqual(sorted(p.name for p in home.iterdir()), ['config.yaml'])


class PluginTests(unittest.TestCase):
    def test_registration_is_offline_and_exposes_no_approval_tool(self):
        ctx = FakeContext()
        load_plugin().register(ctx)
        self.assertEqual(set(ctx.tools), {
            'bili_status', 'bili_read', 'bili_prepare', 'bili_send',
            'bili_verify', 'bili_memory', 'bili_cycle', 'bili_frames',
        })
        self.assertFalse(any('approve' in name or 'login' in name for name in ctx.tools))
        self.assertIn('bili', ctx.commands)
        self.assertIn('bili', ctx.cli)
        for name, tool in ctx.tools.items():
            self.assertEqual(tool['schema']['name'], name)
            self.assertEqual(tool['schema']['parameters']['additionalProperties'], False)
            json.dumps(tool['schema'])
    def test_send_hook_forces_human_approval_only_on_send(self):
        ctx = FakeContext()
        load_plugin().register(ctx)
        guard = ctx.hooks['pre_tool_call']
        self.assertEqual(guard('bili_send', {'draft_id':'d'})['action'], 'approve')
        self.assertIsNone(guard('bili_read', {'action':'video','query':'BV1234567890'}))
    def test_send_approval_rule_is_bound_to_exact_draft_id(self):
        ctx = FakeContext()
        load_plugin().register(ctx)
        guard = ctx.hooks['pre_tool_call']
        first = guard('bili_send', {'draft_id': 'draft-1'})
        second = guard('bili_send', {'draft_id': 'draft-2'})
        self.assertNotEqual(first.get('rule_key'), second.get('rule_key'))
        self.assertEqual(first.get('rule_key'), 'hermes-bilibili:send:draft-1')

    def test_manifest_defaults_match_the_single_policy_model(self):
        import yaml
        from hermes_bilibili.core import Settings
        manifest=yaml.safe_load((ROOT/'plugin.yaml').read_text())
        defaults=Settings().as_dict()
        for key,value in defaults.items():
            with self.subTest(key=key):
                self.assertEqual(manifest['config_schema'][key]['default'],value)


if __name__ == '__main__':
    unittest.main()
