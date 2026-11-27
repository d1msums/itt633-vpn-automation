import tempfile
from pathlib import Path
import unittest
import yaml
import deploy


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.data, self.routers, self.configs = deploy.prepare(deploy.ROOT / 'inventory.yaml', deploy.ROOT / 'tunnel.j2')

    def test_unfinished_template_blocked(self):
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            deploy.require_complete({'R1': '! TODO'})

    def test_redacted_preview_cannot_be_deployed(self):
        with self.assertRaisesRegex(ValueError, 'real VPN key'):
            deploy.require_complete(self.configs)

    def test_complete_template_and_peer_routes(self):
        _, _, configs = deploy.prepare(deploy.ROOT / 'inventory.yaml', deploy.ROOT / 'tunnel.j2', 'TestOnly-Key123')
        deploy.require_complete(configs)
        self.assertIn('ip route 192.168.20.0 255.255.255.0 Tunnel0 172.16.0.2', configs['R1'])
        self.assertIn('ip route 192.168.10.0 255.255.255.0 Tunnel0 172.16.0.1', configs['R2'])
        self.assertIn('peer R2', configs['R1'])
        self.assertIn('peer R1', configs['R2'])
        self.assertNotIn('TestOnly-Key123', ''.join(self.configs.values()))

    def test_key_command_injection_rejected(self):
        for key in ('abc\nend', '', '12345678\n', 'a b c d e f'):
            with self.assertRaises(ValueError):
                deploy.validate_psk(key)

    def test_overlapping_lans_rejected(self):
        self.data['routers']['R2']['lan'] = '192.168.10.2/24'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'inventory.yaml'
            path.write_text(yaml.safe_dump(self.data))
            with self.assertRaisesRegex(ValueError, 'Overlapping'):
                deploy.prepare(path, deploy.ROOT / 'tunnel.j2')

    def test_missing_template_variable_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bad.j2'
            path.write_text('{{ missing_variable }}')
            from jinja2 import UndefinedError
            with self.assertRaises(UndefinedError):
                deploy.prepare(deploy.ROOT / 'inventory.yaml', path)

    def run_fake(self, fail_connect=False, fail_send=False):
        events = []
        class Session:
            def __init__(self, host): self.host = host
            def check_enable_mode(self): return True
            def disconnect(self): events.append(('disconnect', self.host))
            def send_config_set(self, commands, **kwargs):
                events.append(('send', self.host))
                if fail_send and self.host == '10.0.0.2':
                    raise RuntimeError('simulated command rejection')
        def connect(**kwargs):
            host = kwargs['host']
            if fail_connect and host == '10.0.0.2':
                raise TimeoutError('simulated timeout')
            return Session(host)
        report = {'routers': {'R1': 'not attempted', 'R2': 'not attempted'}}
        try:
            deploy.deploy(self.data, self.routers, self.configs, report, connect, 'dummy', '')
        except (TimeoutError, RuntimeError):
            pass
        return events, report

    def test_second_ssh_failure_sends_nothing(self):
        events, report = self.run_fake(fail_connect=True)
        self.assertFalse(any(event[0] == 'send' for event in events))
        self.assertIn(('disconnect', '10.0.0.1'), events)
        self.assertEqual(report['routers']['R1'], 'not attempted')

    def test_partial_failure_preserves_progress_and_disconnects(self):
        events, report = self.run_fake(fail_send=True)
        self.assertEqual(report['routers']['R1'], 'commands sent; VPN verification pending')
        self.assertEqual(report['routers']['R2'], 'attempted; changes may be partial')
        self.assertEqual(sum(event[0] == 'disconnect' for event in events), 2)

    def test_command_success_is_not_vpn_success(self):
        events, report = self.run_fake()
        self.assertEqual(report['status'], 'commands_sent_verification_pending')


if __name__ == '__main__':
    unittest.main()
