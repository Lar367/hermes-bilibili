"""Offline validation in scratch HOME; leaves user profile and staging alone."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
HOME = Path(tempfile.mkdtemp(prefix='bili-compat-host-', dir=os.environ['TMPDIR']))
site = subprocess.check_output([str(ROOT / '.venv/bin/python'), '-c',
                                'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip()
env = {**os.environ, 'HOME': str(HOME), 'USERPROFILE': str(HOME), 'HERMES_HOME': str(HOME),
       'HERMES_BUNDLED_PLUGINS': str(HOME / 'no-bundled-plugins'),
       'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': site + os.pathsep + os.environ.get('PYTHONPATH', '')}
results = []

def run(command, name, cwd=ROOT):
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
    (OUT / (name + '.txt')).write_text(result.stdout + result.stderr)
    results.append({'name': name, 'command': command, 'cwd': str(cwd), 'exit_code': result.returncode,
                    'stdout': result.stdout, 'stderr': result.stderr})
    (OUT / 'verification.json').write_text(json.dumps({'home': str(HOME), 'commands': results},
                                                      ensure_ascii=False, indent=2))
    print(name, 'exit', result.returncode, flush=True)
    if result.returncode:
        print(result.stdout + result.stderr)
        raise SystemExit(result.returncode)
    return result

# Explicitly deny DNS/remote socket connections in the test parent process.
code = '''import socket, unittest

def denied(*args, **kwargs):
    raise AssertionError("real network forbidden during offline suite")
socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.getaddrinfo = denied
suite = unittest.defaultTestLoader.discover("tests")
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
'''
run([sys.executable, '-c', code], 'full-tests')
target = HOME / 'plugins/hermes-bilibili'
run([sys.executable, 'scripts/install_local.py'], 'install')
run(['hermes', 'plugins', 'doctor', str(target), '--ci'], 'doctor')
run(['hermes', 'plugins', 'validate', str(target), '--json'], 'validate')
run(['hermes', 'plugins', 'enable', 'hermes-bilibili', '--no-allow-tool-override'], 'enable')
run([sys.executable, 'scripts/verify_host.py'], 'public-list')
status = run([sys.executable, '-m', 'hermes_bilibili', 'status'], 'installed-status', cwd=target)
data = json.loads(status.stdout)
assert data['success'] and data['data']['writes_enabled'] is False
files = ['hermes_bilibili/client.py', 'hermes_bilibili/media.py', 'tests/test_client.py',
         'tests/test_client_fixes.py', 'tests/test_client_compat.py', 'tests/test_media.py', 'README.md']
hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files}
assert all((ROOT / name).read_bytes() == (target / name).read_bytes()
           for name in ['hermes_bilibili/client.py', 'hermes_bilibili/media.py', 'README.md'])
(OUT / 'post-fix-hashes.json').write_text(json.dumps(hashes, indent=2))
print('verified current installed copy; writes_enabled=false')
