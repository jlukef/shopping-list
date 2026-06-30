import unittest
import os
import re

class DeployExamplesStaticTests(unittest.TestCase):
    def setUp(self):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.systemd_path = os.path.join(base_dir, 'deploy', 'shopping-list.service.example')
        self.caddy_path = os.path.join(base_dir, 'deploy', 'Caddyfile.sharedlist.example')
        
        with open(self.systemd_path, 'r', encoding='utf-8') as f:
            self.systemd_content = f.read()
            
        with open(self.caddy_path, 'r', encoding='utf-8') as f:
            self.caddy_content = f.read()

    def test_systemd_uses_correct_path(self):
        self.assertIn('/srv/shopping-list', self.systemd_content)
        self.assertIn('WorkingDirectory=/srv/shopping-list', self.systemd_content)

    def test_systemd_binds_to_correct_host_and_port(self):
        self.assertIn('127.0.0.1', self.systemd_content)
        self.assertIn('8770', self.systemd_content)
        self.assertTrue(re.search(r'uvicorn\s+src\.shopping_list\.app:app\s+--host\s+127\.0\.0\.1\s+--port\s+8770', self.systemd_content))

    def test_caddy_reverse_proxies_correctly(self):
        self.assertIn('reverse_proxy 127.0.0.1:8770', self.caddy_content)

    def test_no_obvious_secrets(self):
        # Ensure no actual keys are in the examples
        self.assertNotIn('sk-ant', self.systemd_content)
        self.assertNotIn('sk-ant', self.caddy_content)
        self.assertNotIn('BEGIN PRIVATE KEY', self.systemd_content)
        self.assertNotIn('BEGIN PRIVATE KEY', self.caddy_content)
        # Check that it uses EnvironmentFile instead of raw secrets
        self.assertIn('EnvironmentFile=/srv/shopping-list/.env', self.systemd_content)

if __name__ == '__main__':
    unittest.main()
