"""Local takeover launch policy, no browser or network in unit tests."""
import importlib.util
from pathlib import Path
import unittest


class LaunchPolicyTests(unittest.TestCase):
    def load(self):
        spec = importlib.util.spec_from_file_location('takeover', Path(__file__).with_name('session.py'))
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_transport_is_unix_vnc_and_loopback_websocket(self):
        commands = self.load().commands(Path('/run/user/1001/bili-test'), Path('/home/admin/.local/share/bili-test'), '/chrome')
        vnc = commands['vnc']
        self.assertEqual(vnc[vnc.index('-rfbport') + 1], '0')
        self.assertIn('-no6', vnc)
        self.assertIn('-noipv6', vnc)
        self.assertEqual(vnc[vnc.index('-rfbportv6') + 1], '0')
        self.assertIn('-unixsock', vnc)
        self.assertIn('-safer', vnc)
        self.assertIn('-nosel', vnc)
        web = commands['web']
        self.assertIn('127.0.0.1:6080', web)
        self.assertIn('--unix-target=/run/user/1001/bili-test/vnc.sock', web)
        self.assertIn('--auth-plugin=ExpectOrigin', web)
        self.assertIn('--auth-source=http://127.0.0.1:6080 http://localhost:6080', web)

    def test_browser_has_dedicated_profile_and_enabled_sandbox(self):
        commands = self.load().commands(Path('/run/user/1001/bili-test'), Path('/home/admin/.local/share/bili-test'), '/chrome')
        browser = commands['browser']
        self.assertIn('--user-data-dir=/home/admin/.local/share/bili-test', browser)
        self.assertNotIn('--no-sandbox', browser)
        self.assertIn('--disable-extensions', browser)
        self.assertIn('--disable-gpu', browser)
        self.assertIn('--renderer-process-limit=2', browser)
        self.assertIn('--remote-debugging-address=127.0.0.1', browser)
        self.assertIn('--remote-debugging-port=9223', browser)
        self.assertIn('https://passport.bilibili.com/login', browser)
        xvfb = commands['display']
        self.assertIn('-auth', xvfb)
        self.assertIn('-nolisten', xvfb)
        self.assertNotIn('-ac', xvfb)


if __name__ == '__main__':
    unittest.main()
