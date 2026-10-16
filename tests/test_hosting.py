from io import StringIO
import os
import socket
import ssl
import sys
from types import SimpleNamespace
import urllib.error
import unittest
from unittest.mock import MagicMock, Mock, patch

from scripts.host_cloudflare import origin_healthy, probe_origin, start_with_output, stop, wait_for_connector


class LauncherOutputTests(unittest.TestCase):
    def test_hidden_child_public_url_is_relayed_to_parent_terminal(self):
        output = StringIO()
        public_line = "Public URL: https://manu.wiphk.com/"
        process = None
        try:
            with patch("scripts.host_cloudflare.sys.stdout", output):
                process = start_with_output([sys.executable, "-c", f"print({public_line!r}, flush=True)"],
                                            creationflags=0x08000000 if os.name == "nt" else 0)
                self.assertEqual(process.wait(timeout=10), 0)
                process._output_thread.join(timeout=3)
            self.assertIn(public_line, output.getvalue())
        finally:
            stop(process)


class OriginHealthTests(unittest.TestCase):
    def test_dns_failure_can_probe_verified_tls_using_public_dns(self):
        dns_error = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
        dns_response = MagicMock()
        dns_response.__enter__.return_value.read.return_value = b'{"Status":0,"Answer":[{"type":1,"data":"104.16.230.132"}]}'
        context = MagicMock()
        connection = MagicMock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.return_value = b"<h1>A picture in.</h1>"
        with patch("urllib.request.urlopen", side_effect=[dns_error, dns_response]), patch("socket.create_connection") as connect, patch("ssl.create_default_context", return_value=context), patch("http.client.HTTPSConnection", return_value=connection):
            self.assertTrue(origin_healthy("https://test-ready.trycloudflare.com"))
            connect.assert_called_once_with(("104.16.230.132", 443), timeout=3)
            self.assertEqual(context.wrap_socket.call_args.kwargs["server_hostname"], "test-ready.trycloudflare.com")
            connection.request.assert_called_once_with("GET", "/", headers={"Host": "test-ready.trycloudflare.com"})

    def test_certificate_failures_do_not_trigger_dns_fallback(self):
        error = urllib.error.URLError(ssl.SSLCertVerificationError("invalid certificate"))
        with patch("urllib.request.urlopen", side_effect=error), patch("scripts.host_cloudflare.resolved_origin_response") as fallback:
            self.assertFalse(origin_healthy("https://test-ready.trycloudflare.com"))
            fallback.assert_not_called()

    def test_http_failure_reports_status_and_closes_response(self):
        error = urllib.error.HTTPError("https://test-ready.trycloudflare.com/", 502, "Bad Gateway", {}, StringIO("bad gateway"))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(OSError, "HTTP 502"):
                probe_origin("https://test-ready.trycloudflare.com")
        self.assertTrue(error.fp.closed)

    def test_wrong_app_is_not_healthy(self):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        response.__enter__.return_value.read.return_value = b"unrelated site"
        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(OSError, "other than the desktop app"):
                probe_origin("https://test-ready.trycloudflare.com")

    def test_public_dns_cannot_redirect_probe_to_private_network(self):
        error = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"Status":0,"Answer":[{"type":1,"data":"127.0.0.1"}]}'
        with patch("urllib.request.urlopen", side_effect=[error, response]), patch("socket.create_connection") as connect:
            self.assertFalse(origin_healthy("https://test-ready.trycloudflare.com"))
            connect.assert_not_called()


class ConnectorReadinessTests(unittest.TestCase):
    def process(self, output):
        return SimpleNamespace(stdout=StringIO(output), poll=Mock(return_value=None))

    def test_url_without_registration_is_not_ready(self):
        process = self.process("https://test-ready.trycloudflare.com\n")
        with self.assertRaisesRegex(RuntimeError, "ready|establish"):
            wait_for_connector(process, timeout=0.02)

    def test_registered_connector_must_serve_the_actual_app(self):
        process = self.process("https://test-ready.trycloudflare.com\nRegistered tunnel connection connIndex=0\n")
        with patch("scripts.host_cloudflare.probe_origin", side_effect=OSError("wrong page")) as health:
            with self.assertRaisesRegex(RuntimeError, "wrong page"):
                wait_for_connector(process, timeout=0.02)
            health.assert_called()

    def test_registration_and_successful_origin_check_are_ready(self):
        process = self.process("https://test-ready.trycloudflare.com\nRegistered tunnel connection connIndex=0\n")
        with patch("scripts.host_cloudflare.probe_origin", return_value=None) as health:
            self.assertEqual(wait_for_connector(process, timeout=0.2), "https://test-ready.trycloudflare.com")
            health.assert_called_once_with("https://test-ready.trycloudflare.com")
