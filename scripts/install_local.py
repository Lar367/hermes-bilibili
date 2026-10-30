"""Copy a reviewed local release into the active profile; never enable or overwrite."""
import os
from pathlib import Path
import shutil
import sys
import tempfile


def main():
    try:
        from hermes_constants import get_hermes_home
    except ImportError:
        print('Run with the Python interpreter from your Hermes installation.', file=sys.stderr)
        return 2
    source = Path(__file__).resolve().parents[1]
    parent = get_hermes_home() / 'plugins'
    target = parent / 'hermes-bilibili'
    if target.exists() or target.is_symlink():
        print('Plugin target already exists; refusing to overwrite: ' + str(target), file=sys.stderr)
        return 2
    if any(p.is_symlink() for p in (parent, *parent.parents)):
        print('Symlink plugin destination rejected.', file=sys.stderr)
        return 2
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.bilibili-install-', dir=parent))
    try:
        for name in ('__init__.py', 'plugin.yaml', 'README.md', 'LICENSE'):
            shutil.copy2(source / name, staging / name)
        for name in ('hermes_bilibili', 'skills'):
            tree = source / name
            if any(p.is_symlink() for p in tree.rglob('*')):
                raise ValueError('Symlink release payload rejected')
            shutil.copytree(tree, staging / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        os.rename(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print('Installed disabled (no configuration changed): ' + str(target))
    print('Validate and enable explicitly: hermes plugins doctor ' + str(target) + ' --ci; hermes plugins enable hermes-bilibili')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
