"""Operator CLI for host-isolated Hermes installations."""
import argparse
import json


class HostContext:
    def get_config(self, key, default=None):
        from hermes_cli.config import load_config_readonly
        entries = load_config_readonly().get('plugins', {}).get('entries', {})
        return entries.get('hermes-bilibili', {}).get('settings', {}).get(key, default)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Hermes Bilibili: status / read / draft / approve / reject / send / verify / cycle')
    parser.add_argument('arguments', nargs='*')
    args = parser.parse_args(argv)
    from .integration import cli_command
    return cli_command(HostContext(), args.arguments)
