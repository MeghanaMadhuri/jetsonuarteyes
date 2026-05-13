"""Smoke import for Wi‑Fi / config helpers used by the tablet gateway."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nina.jetson_net.config import LinkDaemonConfig, load_config
from nina.jetson_net.mdns_advertise import _sanitize_instance_name, gather_ipv4_for_mdns
from nina.jetson_net.nm import mock_backend
from nina.jetson_net.state import LinkCoordinator


class TestJetsonNetImports(unittest.TestCase):
    def test_load_config_and_coordinator(self) -> None:
        c = load_config()
        co = LinkCoordinator(c, mock_backend())
        self.assertIsNotNone(co.cfg.host)

    def test_record_http_client_debounces_disk_writes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "link_state.json"
            cfg = LinkDaemonConfig(state_path=p, mock_nm=True)
            co = LinkCoordinator(cfg, mock_backend())
            saves: list[int] = []
            real_save = co.store.save

            def counting_save(state) -> None:  # noqa: ANN001
                saves.append(1)
                return real_save(state)

            co.store.save = counting_save  # type: ignore[method-assign]

            t0 = 10_000.0
            with patch("nina.jetson_net.state.time.monotonic", return_value=t0):
                co.record_http_client("10.0.0.1")
            self.assertEqual(len(saves), 1)

            with patch("nina.jetson_net.state.time.monotonic", return_value=t0 + 0.5):
                co.record_http_client("10.0.0.2")
            self.assertEqual(len(saves), 1)

            with patch("nina.jetson_net.state.time.monotonic", return_value=t0 + 2.5):
                co.record_http_client("10.0.0.2")
            self.assertEqual(len(saves), 2)

    def test_mdns_sanitize_instance_name(self) -> None:
        self.assertEqual(_sanitize_instance_name("  my bot!  "), "my bot")
        self.assertEqual(_sanitize_instance_name(""), "nina")

    def test_gather_ipv4_for_mdns_returns_list(self) -> None:
        cfg = LinkDaemonConfig(mock_nm=True)
        co = LinkCoordinator(cfg, mock_backend())
        ips = gather_ipv4_for_mdns(co)
        self.assertIsInstance(ips, list)
        for ip in ips:
            self.assertFalse(ip.startswith("127."), ip)


if __name__ == "__main__":
    unittest.main()
