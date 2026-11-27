from ipaddress import IPv4Interface
from pathlib import Path
import sys
import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

folder = Path(__file__).resolve().parent

def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else folder / 'inventory.yaml'
    data = yaml.safe_load(path.read_text())
    routers = {}
    for name in ('R1', 'R2'):
        routers[name] = {role: IPv4Interface(data['routers'][name][role])
                         for role in ('lan', 'wan', 'management', 'tunnel')}
    a, b = routers.values()
    if a['lan'].network.overlaps(b['lan'].network):
        raise ValueError('HQ and branch LANs overlap')
    for role in ('management', 'tunnel'):
        if a[role].network != b[role].network or a[role].ip == b[role].ip:
            raise ValueError(f'{role} must use distinct addresses in the same subnet')
    template = Environment(loader=FileSystemLoader(folder), undefined=StrictUndefined).get_template('tunnel.j2')
    for name, peer in [('R1', 'R2'), ('R2', 'R1')]:
        print(template.render(name=name, peer_name=peer, local=routers[name], peer=routers[peer], interfaces=data['interfaces'], vpn_psk='<REDACTED>'))
    print('Offline address checks passed. Live verification pending.')

if __name__ == '__main__':
    try:
        main()
    except (KeyError, TypeError, ValueError, OSError, yaml.YAMLError) as error:
        sys.exit(f'Input error: {error}')
