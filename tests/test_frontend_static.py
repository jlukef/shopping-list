import unittest
import os
import re

class FrontendStaticTests(unittest.TestCase):
    def setUp(self):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.index_html_path = os.path.join(base_dir, 'docs', 'index.html')
        self.app_js_path = os.path.join(base_dir, 'docs', 'app.js')
        
        with open(self.index_html_path, 'r', encoding='utf-8') as f:
            self.html_content = f.read()
            
        with open(self.app_js_path, 'r', encoding='utf-8') as f:
            self.js_content = f.read()

    def test_logout_button_exists_and_hidden(self):
        # logout button is present and hidden by default
        self.assertIn('id="logoutBtn"', self.html_content)
        self.assertIn('class="iconBtn hidden"', self.html_content)
        self.assertTrue(re.search(r'<button\s+id="logoutBtn"\s+class="iconBtn hidden"', self.html_content))

    def test_receipts_tab_exists(self):
        # Receipts tab and receiptsTab exist
        self.assertIn('data-tab="receipts"', self.html_content)
        self.assertIn('id="receiptsTab"', self.html_content)

    def test_receipts_history_segments_exist(self):
        # Receipts/History segment buttons exist
        self.assertIn('data-segment="receiptsView"', self.html_content)
        self.assertIn('data-segment="historyView"', self.html_content)

    def test_suggestions_strip_is_hidden(self):
        # suggestions strip is hidden by default
        self.assertIn('id="suggestionsStrip"', self.html_content)
        self.assertTrue(re.search(r'id="suggestionsStrip"\s+class="suggestionsStrip hidden"', self.html_content))

    def test_is_hosted_mode_logic(self):
        # isHostedMode() exists and requires :8770 for localhost/127.0.0.1
        self.assertIn('function isHostedMode()', self.js_content)
        self.assertIn("location.port === '8770'", self.js_content)
        self.assertIn("host === '127.0.0.1'", self.js_content)
        self.assertIn("host === 'localhost'", self.js_content)

    def test_default_hosted_api_path(self):
        # default hosted API path is /api
        self.assertIn("const DEFAULT_API_URL = isHostedMode() ? '/api' : '';", self.js_content)
        self.assertIn("get apiUrl()      { return DEFAULT_API_URL || this.scriptUrl; }", self.js_content)

    def test_fresh_start_ignores_removed_shop_preferences(self):
        self.assertIn("localStorage.getItem('defaultShop') || 'morrisons'", self.js_content)
        self.assertIn("JSON.parse(saved).filter(id => validIds.has(id))", self.js_content)

    def test_preview_toggle_updates_accessibility_state(self):
        self.assertIn("preview.setAttribute('aria-hidden', show ? 'false' : 'true');", self.js_content)

if __name__ == '__main__':
    unittest.main()
