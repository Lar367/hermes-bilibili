"""One-hour manual browser takeover. SSH is the authentication boundary.

No automatic login or CAPTCHA handling. noVNC is loopback-only, raw VNC is a
private Unix socket, CDP is loopback-only and not part of the forwarded port.
"""
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import time
import urllib.request

DISPLAY = ':93'


def commands(runtime, profile, chrome):
    auth = str(runtime / 'Xauthority')
    return {
        'display': ['Xvfb', DISPLAY, '-screen', '0', '1280x800x24', '-nolisten', 'tcp', '-auth', auth, '-noreset'],
        'vnc': ['x11vnc', '-norc', '-display', DISPLAY, '-auth', auth,
                '-unixsock', str(runtime / 'vnc.sock'), '-rfbport', '0', '-rfbportv6', '0', '-no6', '-noipv6',
                '-listen', '127.0.0.1', '-listen6', '::1',
                '-forever', '-noshared', '-dontdisconnect', '-safer', '-nocmds',
                '-nosel', '-nopw', '-xkb'],
        'web': ['websockify', '--web=/usr/share/novnc', '--file-only',
                '--auth-plugin=ExpectOrigin',
                '--auth-source=http://127.0.0.1:6080 http://localhost:6080',
                '--unix-target=' + str(runtime / 'vnc.sock'), '127.0.0.1:6080'],
        'browser': [chrome, '--user-data-dir=' + str(profile),
                    '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=9223',
                    '--no-first-run', '--no-default-browser-check', '--disable-sync',
                    '--disable-extensions', '--disable-gpu', '--renderer-process-limit=2',
                    '--disable-background-networking', '--disable-background-mode',
                    '--disable-session-crashed-bubble', '--password-store=basic',
                    '--start-maximized', '--window-size=1280,800',
                    'https://passport.bilibili.com/login'],
    }


def private_dir(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise RuntimeError('Symlink directory refused')
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)


def main():
    os.umask(0o077)
    runtime = Path('/run/user') / str(os.getuid()) / 'bili-browser-takeover'
    profile = Path.home() / '.local/share/hermes-bilibili-browser'
    private_dir(runtime)
    private_dir(profile)
    for port in (6080, 9223):
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('127.0.0.1', port))
    if Path('/tmp/.X93-lock').exists():
        raise RuntimeError('Display already in use; refusing to replace it')
    for name in ('vnc.sock', 'Xauthority'):
        (runtime / name).unlink(missing_ok=True)
    # The X cookie stays out of argv, logs and the model context.
    proc = subprocess.run(['xauth', '-f', str(runtime / 'Xauthority')],
                          input='add ' + DISPLAY + ' . ' + secrets.token_hex(16) + '\n',
                          text=True, capture_output=True)
    if proc.returncode:
        raise RuntimeError('Failed to prepare display authentication')
    os.chmod(runtime / 'Xauthority', 0o600)
    candidates = sorted((Path.home() / '.cache/ms-playwright').glob('chromium-*/chrome-linux64/chrome'))
    if not candidates:
        raise RuntimeError('Packaged Chromium not found')
    chrome = str(candidates[-1])
    plan = commands(runtime, profile, chrome)
    env = {**os.environ, 'DISPLAY': DISPLAY, 'XAUTHORITY': str(runtime / 'Xauthority')}
    children = []
    logs = []
    stop = False

    def request_stop(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    def launch(name):
        log = (runtime / (name + '.log')).open('ab', buffering=0)
        logs.append(log)
        child = subprocess.Popen(plan[name], env=env, stdout=log, stderr=subprocess.STDOUT)
        children.append((name, child))
        return child

    def wait_for(check, seconds=20):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if stop or any(child.poll() is not None for _, child in children):
                raise RuntimeError('Browser component stopped during startup')
            try:
                if check():
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.2)
        raise RuntimeError('Startup timed out')

    try:
        launch('display')
        wait_for(lambda: Path('/tmp/.X11-unix/X93').exists())
        launch('vnc')
        wait_for(lambda: (runtime / 'vnc.sock').exists())
        os.chmod(runtime / 'vnc.sock', 0o600)
        launch('browser')
        def cdp_ready():
            with urllib.request.urlopen('http://127.0.0.1:9223/json/version', timeout=1) as response:
                return bool(json.load(response).get('webSocketDebuggerUrl'))
        wait_for(cdp_ready, 40)
        launch('web')
        def web_ready():
            with urllib.request.urlopen('http://127.0.0.1:6080/vnc.html', timeout=1) as response:
                return response.status == 200
        wait_for(web_ready)
        print(json.dumps({'ready': True, 'url': 'http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale',
                          'profile': str(profile), 'session_lifetime_seconds': 3600}), flush=True)
        deadline = time.monotonic() + 3600
        while not stop and time.monotonic() < deadline:
            if any(child.poll() is not None for _, child in children):
                raise RuntimeError('Browser component stopped')
            time.sleep(0.5)
    finally:
        for _, child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for _, child in reversed(children):
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for log in logs:
            log.close()
        (runtime / 'vnc.sock').unlink(missing_ok=True)
        (runtime / 'Xauthority').unlink(missing_ok=True)


if __name__ == '__main__':
    main()
