"""Offline host probe, allowed only in an explicitly isolated scratch HOME."""
import json
import os
from pathlib import Path
import sys


def main():
    home = Path(os.environ.get('HERMES_HOME', '')).absolute()
    scratch = Path(os.environ.get('TMPDIR', '')).resolve()
    if (not os.environ.get('TMPDIR') or not scratch.is_dir()
            or not os.environ.get('HERMES_HOME') or not home.resolve().is_relative_to(scratch)
            or home.resolve() == scratch or Path(os.environ.get('HOME', '')).absolute() != home
            or any(p.is_symlink() for p in (home, *home.parents))):
        print('refusing non-scratch HERMES_HOME', file=sys.stderr)
        return 2
    import subprocess
    result = subprocess.run(['hermes', 'plugins', 'list', '--user', '--json'],
                            capture_output=True, text=True, timeout=60, check=True)
    plugins = [p for p in json.loads(result.stdout) if p['name'] == 'hermes-bilibili']
    print(json.dumps({'isolated_home': str(home), 'plugins': plugins}, default=str))
    if len(plugins) != 1 or plugins[0].get('status') != 'enabled':
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
