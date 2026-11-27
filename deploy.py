"""Offline preview by default; explicitly gated live deployment for the lab."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
from getpass import getpass
from ipaddress import IPv4Interface
import json
from pathlib import Path
import re
import time

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parent


def prepare(inventory, template, vpn_psk='<REDACTED>'):
    data = yaml.safe_load(inventory.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Inventory must contain a YAML mapping.')
    routers = {}
    for name in ('R1', 'R2'):
        routers[name] = {}
        for role in ('lan', 'wan', 'management', 'tunnel'):
            address = IPv4Interface(data['routers'][name][role])
            if address.network.prefixlen > 30 or address.ip in (
                address.network.network_address, address.network.broadcast_address
            ):
                raise ValueError(f'{name} {role}: use a usable host address with prefix /30 or shorter.')
            routers[name][role] = address
    a, b = routers.values()
    for role in ('management', 'tunnel'):
        if a[role].network != b[role].network or a[role].ip == b[role].ip:
            raise ValueError(f'{role}: use distinct addresses in the same subnet.')
    entries = [(name, role, address) for name, values in routers.items() for role, address in values.items()]
    for i, (name, role, address) in enumerate(entries):
        for other_name, other_role, other in entries[i + 1:]:
            if role == other_role and role in ('management', 'tunnel'):
                continue
            if address.network.overlaps(other.network):
                raise ValueError(f'Overlapping subnets: {name} {role} and {other_name} {other_role}.')
    interfaces = data['interfaces']
    values = [interfaces[role] for role in ('lan', 'wan', 'management', 'tunnel')]
    if any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9/.-]*', v) for v in values):
        raise ValueError('Invalid interface name.')
    if len(set(values)) != 4:
        raise ValueError('Each interface role must use a different interface.')
    ssh = data['ssh']
    if ssh['device_type'] != 'cisco_xe' or not isinstance(ssh['username'], str) or not ssh['username'].strip():
        raise ValueError('Set cisco_xe and a non-empty SSH username.')
    if type(ssh['port']) is not int or not 1 <= ssh['port'] <= 65535:
        raise ValueError('SSH port must be an integer from 1 to 65535.')
    env = Environment(loader=FileSystemLoader(template.parent), undefined=StrictUndefined)
    jinja = env.get_template(template.name)
    configs = {
        name: jinja.render(name=name, peer_name=peer, local=routers[name], peer=routers[peer], interfaces=interfaces, vpn_psk=vpn_psk)
        for name, peer in [('R1', 'R2'), ('R2', 'R1')]
    }
    return data, routers, configs


def require_complete(configs, allow_redacted=False):
    for name, config in configs.items():
        if re.search(r'pending|offline addressing preview|TODO|PLACEHOLDER', config, re.I):
            raise ValueError(f'{name}: template is incomplete. Add the manually verified VPN configuration first.')
        required = ('crypto ikev2 proposal ', 'crypto ikev2 policy ', 'crypto ikev2 keyring ',
                    'crypto ikev2 profile ', 'crypto ipsec transform-set ', 'crypto ipsec profile ',
                    'tunnel protection ipsec profile ')
        if any(item not in config for item in required):
            raise ValueError(f'{name}: required IKEv2/IPsec sections are missing.')
        if not allow_redacted and '<REDACTED>' in config:
            raise ValueError(f'{name}: a real VPN key is required before deployment.')


def validate_psk(value):
    if not re.fullmatch(r'[A-Za-z0-9!@#$%^&*_.+=:-]{8,127}', value):
        raise ValueError('Use a shared lab VPN key of 8-127 characters: letters, digits or !@#$%^&*_.+=:-; no spaces.')
    return value


def deploy(data, routers, configs, report, connect, password, secret):
    # Open both SSH sessions before sending any configuration.
    with ExitStack() as stack:
        sessions = {}
        for name in ('R1', 'R2'):
            report['stage'] = f'{name}: SSH preflight'
            session = connect(host=str(routers[name]['management'].ip),
                              **data['ssh'], password=password, secret=secret,
                              conn_timeout=15, auth_timeout=15, banner_timeout=20)
            stack.callback(session.disconnect)
            if not session.check_enable_mode():
                session.enable()
            sessions[name] = session
        for name, session in sessions.items():
            report['stage'] = f'{name}: sending configuration'
            report['routers'][name] = 'attempted; changes may be partial'
            commands = [line.strip() for line in configs[name].splitlines()
                        if line.strip() and not line.strip().startswith('!')]
            session.send_config_set(commands, cmd_verify=True, read_timeout=60,
                                    error_pattern=r'(?im)^\s*%\s*(?:Invalid|Incomplete|Ambiguous|Error|Failed)')
            report['routers'][name] = 'commands sent; VPN verification pending'
    report['status'] = 'commands_sent_verification_pending'
    report['stage'] = 'complete'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, default=ROOT / 'inventory.yaml')
    parser.add_argument('--template', type=Path, default=ROOT / 'tunnel.j2')
    parser.add_argument('--deploy', action='store_true')
    parser.add_argument('--lab-ready', action='store_true', help='Confirm manual VPN, clean baseline and management access are ready.')
    args = parser.parse_args()
    started = time.perf_counter()
    report = {'started_utc': datetime.now(timezone.utc).isoformat(), 'status': 'failed',
              'stage': 'input validation', 'routers': {'R1': 'not attempted', 'R2': 'not attempted'},
              'vpn_verified': False}
    result = 1
    try:
        data, routers, configs = prepare(args.inventory, args.template)
        if not args.deploy:
            for config in configs.values():
                print(config)
            print('Offline checks passed. No SSH connection made. VPN verification pending.')
            report['status'] = 'offline_preview_passed'
            report['stage'] = 'complete'
        else:
            report['stage'] = 'deployment readiness checks'
            require_complete(configs, allow_redacted=True)
            if not args.lab_ready:
                raise ValueError('Live deployment also requires --lab-ready after the manual VPN and baseline are ready.')
            from netmiko import ConnectHandler
            vpn_psk = validate_psk(getpass('Shared VPN pre-shared key for BOTH routers (not saved): '))
            data, routers, configs = prepare(args.inventory, args.template, vpn_psk)
            require_complete(configs)
            password = getpass('SSH password (not saved): ')
            secret = getpass('Enable password (Enter if not needed): ')
            deploy(data, routers, configs, report, ConnectHandler, password, secret)
            print('Commands sent to both routers. Ask Aisyah to verify the VPN before saving configuration.')
        result = 0
    except (Exception, KeyboardInterrupt) as error:
        # Exception messages can include credentials or rejected configuration; do not log them.
        report['error_type'] = type(error).__name__
        if isinstance(error, ValueError) and report['stage'] in ('input validation', 'deployment readiness checks'):
            print(f'Stopped: {error}')
        else:
            print(f'Stopped at {report["stage"]}: {type(error).__name__}. Check input, dependencies, SSH credentials or router console as appropriate.')
        print('No automatic rollback or startup-config save was performed. If sending started, inspect both routers before retrying.')
    finally:
        report['elapsed_seconds'] = round(time.perf_counter() - started, 3)
        folder = ROOT / 'runs'
        folder.mkdir(exist_ok=True)
        path = folder / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(f'Run record: {path}')
    return result


if __name__ == '__main__':
    raise SystemExit(main())
