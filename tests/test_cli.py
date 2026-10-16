import unittest

from accessible_ai.__main__ import validate_public_url


class PublicUrlTests(unittest.TestCase):
    def test_valid_https_origins(self):
        for value in ("https://manu.wiphk.com", "https://example.com:8443/", "https://[::1]:8443"):
            with self.subTest(value=value):
                self.assertEqual(validate_public_url(value), value.rstrip("/"))

    def test_invalid_hosts_ports_and_non_origins(self):
        for value in ("https://:443", "https://example.com:abc", "https://example.com:70000", "https://example.com:0", "https://example.com:", "https://bad host.com", "https://[bad", "https://user:pass@example.com", "https://example.com/path", "https://example.com?query=1", "https://example.com#token=secret", "http://example.com"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "HTTPS origin"):
                    validate_public_url(value)
