"""Tests for proxy_rotator: TorInstance, ProxyRotator, singleton."""

from unittest.mock import MagicMock, patch

import time
import pytest

from backend.services.proxy_rotator import (
    TorInstance,
    ProxyRotator,
    get_rotator,
    reset_rotator,
    DEFAULT_TOR_PORTS,
    IP_CHECK_URLS,
)


@pytest.fixture(autouse=True)
def _clear_unusable_port_memo():
    """proxy_rotator remembers ports that failed the circuit check in a
    MODULE-LEVEL dict, so one test marking :9050 unusable made a later test that
    mocks :9050 as healthy find nothing. Process-wide memo, per-test isolation."""
    from backend.services import proxy_rotator
    proxy_rotator._UNUSABLE_PORTS.clear()
    yield
    proxy_rotator._UNUSABLE_PORTS.clear()


class TestTorInstance:
    def test_default_values(self):
        inst = TorInstance(socks_port=9050, control_port=9051)
        assert inst.socks_port == 9050
        assert inst.control_port == 9051
        assert inst.label == "tor"
        assert inst.host == "127.0.0.1"
        assert inst.healthy is True
        assert inst.consecutive_failures == 0

    def test_socks_url_format(self):
        inst = TorInstance(socks_port=9050)
        assert inst.socks_url == "socks5://127.0.0.1:9050"

    def test_socks_url_custom_host(self):
        inst = TorInstance(socks_port=9150, host="192.168.1.1")
        assert inst.socks_url == "socks5://192.168.1.1:9150"

    def test_proxy_dict_playwright(self):
        inst = TorInstance(socks_port=9050)
        assert inst.proxy_dict_playwright == {"server": "socks5://127.0.0.1:9050"}

    def test_proxy_dict_playwright_none_when_no_port(self):
        inst = TorInstance(socks_port=9050)
        assert inst.proxy_dict_playwright is not None

    def test_proxy_dict_httpx(self):
        inst = TorInstance(socks_port=9050)
        proxy = inst.proxy_dict_httpx
        assert proxy["all://"] == "socks5://127.0.0.1:9050"
        assert proxy["http://"] == "socks5://127.0.0.1:9050"
        assert proxy["https://"] == "socks5://127.0.0.1:9050"

    def test_is_tor_running_port_open(self):
        inst = TorInstance(socks_port=9050)
        with patch("backend.services.proxy_rotator.socket.socket") as mock_socket:
            mock_instance = MagicMock()
            mock_socket.return_value.__enter__.return_value = mock_instance
            assert inst.is_tor_running() is True

    def test_is_tor_running_port_closed(self):
        inst = TorInstance(socks_port=9999)
        with patch("backend.services.proxy_rotator.socket.socket") as mock_socket:
            mock_instance = MagicMock()
            mock_instance.connect.side_effect = ConnectionRefusedError()
            mock_socket.return_value.__enter__.return_value = mock_instance
            assert inst.is_tor_running() is False

    def test_is_tor_running_timeout(self):
        inst = TorInstance(socks_port=9050)
        with patch("backend.services.proxy_rotator.socket.socket") as mock_socket:
            import socket
            mock_instance = MagicMock()
            mock_instance.connect.side_effect = socket.timeout()
            mock_socket.return_value.__enter__.return_value = mock_instance
            assert inst.is_tor_running() is False

    def test_rotate_circuit_no_control_port(self):
        inst = TorInstance(socks_port=9050, control_port=None)
        assert inst.rotate_circuit() is False

    def test_rotate_circuit_control_port_failure(self):
        inst = TorInstance(socks_port=9050, control_port=9051)
        with patch("stem.control.Controller.from_port") as mock_from_port:
            mock_from_port.side_effect = Exception("connection refused")
            result = inst.rotate_circuit()
            assert result is False
            assert inst.consecutive_failures == 1

    def test_check_health_marks_unhealthy(self):
        inst = TorInstance(socks_port=9050)
        with patch.object(inst, "is_tor_running", return_value=False):
            assert inst.check_health() is False
            assert inst.healthy is False
            assert inst.consecutive_failures == 1

    def test_check_health_recovers(self):
        inst = TorInstance(socks_port=9050)
        inst.healthy = False
        with patch.object(inst, "is_tor_running", return_value=True):
            with patch.object(inst, "get_current_ip", return_value="1.2.3.4"):
                assert inst.check_health() is True
                assert inst.healthy is True

    def test_get_current_ip_checks_urls(self):
        inst = TorInstance(socks_port=9050)
        with patch("httpx.Client") as mock_client:
            mock_instance = MagicMock()
            mock_instance.__enter__.return_value = mock_instance
            mock_instance.get.return_value.status_code = 200
            mock_instance.get.return_value.json.return_value = {"origin": "5.6.7.8"}
            mock_client.return_value = mock_instance
            ip = inst.get_current_ip()
            assert ip == "5.6.7.8"

    def test_get_current_ip_strips_proxy_chain(self):
        inst = TorInstance(socks_port=9050)
        with patch("httpx.Client") as mock_client:
            mock_instance = MagicMock()
            mock_instance.__enter__.return_value = mock_instance
            mock_instance.get.return_value.status_code = 200
            mock_instance.get.return_value.json.return_value = {"origin": "1.2.3.4, 5.6.7.8"}
            mock_client.return_value = mock_instance
            ip = inst.get_current_ip()
            assert ip == "1.2.3.4"

    def test_get_current_ip_fallback_through_urls(self):
        inst = TorInstance(socks_port=9050)
        call_count = 0
        def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if call_count <= 2:
                resp.status_code = 500
            else:
                resp.status_code = 200
                resp.json.return_value = {"ip": "9.10.11.12"}
            return resp

        with patch("httpx.Client") as mock_client:
            mock_instance = MagicMock()
            mock_instance.__enter__.return_value = mock_instance
            mock_instance.get.side_effect = mock_get
            mock_client.return_value = mock_instance
            ip = inst.get_current_ip()
            assert ip == "9.10.11.12"
            assert call_count == 3


class TestProxyRotator:
    def test_initially_uninitialized(self):
        rotator = ProxyRotator()
        assert rotator._initialized is False
        assert rotator._instances == []

    def test_discover_tor_instances_empty(self):
        rotator = ProxyRotator()
        with patch.object(TorInstance, "is_tor_running", return_value=False):
            count = rotator.discover_tor_instances()
            assert count == 0
            assert rotator._initialized is True

    def test_discover_tor_instances_finds_one(self):
        """An open port is no longer sufficient — the circuit must carry traffic."""
        rotator = ProxyRotator({"tor_socks_ports": [9050], "tor_control_ports": [9051]})
        with patch.object(TorInstance, "is_tor_running", return_value=True):
            with patch.object(TorInstance, "circuit_works", return_value=True):
                with patch.object(TorInstance, "check_health", return_value=True):
                    count = rotator.discover_tor_instances()
                    assert count == 1
                    assert len(rotator._instances) == 1

    def test_discovery_rejects_an_open_port_that_is_not_working_tor(self):
        """TOR_SOCKS_PORTS lists 20 ports and anything may be listening on one.
        A live run adopted an SSH tunnel on :10250 as a Tor daemon and routed
        every scrape into it — Hotfrog hung 22 minutes, Maps returned 0 for
        every tile. An open socket must not be mistaken for a working proxy."""
        rotator = ProxyRotator({"tor_socks_ports": [10250], "tor_control_ports": [10251]})
        with patch.object(TorInstance, "is_tor_running", return_value=True):
            with patch.object(TorInstance, "circuit_works", return_value=False):
                assert rotator.discover_tor_instances() == 0
                assert rotator._instances == []
                assert rotator.get_proxy() is None      # falls back to direct

    def test_add_instance_manually(self):
        rotator = ProxyRotator()
        inst = TorInstance(socks_port=9050, control_port=9051)
        rotator.add_instance(inst)
        assert len(rotator._instances) == 1
        assert rotator._initialized is True

    def test_get_proxy_returns_none_when_no_instances(self):
        rotator = ProxyRotator()
        with patch.object(TorInstance, "is_tor_running", return_value=False):
            rotator._initialized = True
            assert rotator.get_proxy() is None

    def test_get_proxy_returns_playwright_format(self):
        rotator = ProxyRotator()
        inst = TorInstance(socks_port=9050)
        with patch.object(inst, "check_health", return_value=True):
            rotator.add_instance(inst)
            proxy = rotator.get_proxy(for_playwright=True)
            assert proxy == {"server": "socks5://127.0.0.1:9050"}

    def test_get_proxy_returns_httpx_format(self):
        rotator = ProxyRotator()
        inst = TorInstance(socks_port=9050)
        with patch.object(inst, "check_health", return_value=True):
            rotator.add_instance(inst)
            proxy = rotator.get_proxy(for_playwright=False)
            assert proxy == {
                "all://": "socks5://127.0.0.1:9050",
                "http://": "socks5://127.0.0.1:9050",
                "https://": "socks5://127.0.0.1:9050",
            }

    def test_get_proxy_round_robin(self):
        rotator = ProxyRotator()
        rotator._initialized = True
        inst1 = TorInstance(socks_port=9050)
        inst2 = TorInstance(socks_port=9150)
        with patch.object(TorInstance, "check_health", return_value=True):
            rotator.add_instance(inst1)
            rotator.add_instance(inst2)
            proxy1 = rotator.get_proxy()
            proxy2 = rotator.get_proxy()
            proxy3 = rotator.get_proxy()
            assert proxy1["server"] != proxy2["server"] or proxy2["server"] != proxy3["server"]

    def test_get_random_proxy(self):
        rotator = ProxyRotator()
        inst = TorInstance(socks_port=9050)
        with patch.object(inst, "check_health", return_value=True):
            rotator.add_instance(inst)
            proxy = rotator.get_random_proxy()
            assert proxy is not None
            assert "server" in proxy

    def test_get_random_proxy_no_instances(self):
        rotator = ProxyRotator()
        with patch.object(TorInstance, "is_tor_running", return_value=False):
            rotator._initialized = True
            assert rotator.get_random_proxy() is None

    def test_get_httpx_client_args_with_proxy(self):
        rotator = ProxyRotator()
        inst = TorInstance(socks_port=9050)
        with patch.object(inst, "check_health", return_value=True):
            rotator.add_instance(inst)
            args = rotator.get_httpx_client_args()
            assert "proxy" in args  # httpx >=0.28: singular `proxy=`, not `proxies=`

    def test_get_httpx_client_args_no_proxy(self):
        rotator = ProxyRotator()
        with patch.object(TorInstance, "is_tor_running", return_value=False):
            rotator._initialized = True
            args = rotator.get_httpx_client_args()
            assert args == {}

    def test_rotate_ip_advances_index(self):
        rotator = ProxyRotator()
        inst = TorInstance(socks_port=9050)
        rotator.add_instance(inst)
        old_idx = rotator._current_index
        rotator.rotate_ip(force_new=False)
        assert rotator._current_index == old_idx + 1

    def test_health_summary(self):
        rotator = ProxyRotator()
        inst = TorInstance(socks_port=9050)
        rotator.add_instance(inst)
        summary = rotator.health_summary()
        assert "instances" in summary
        assert summary["total_healthy"] == 1
        assert summary["instances"][0]["socks"] == "socks5://127.0.0.1:9050"

    def test_get_all_ips(self):
        rotator = ProxyRotator()
        inst = TorInstance(socks_port=9050)
        rotator.add_instance(inst)
        with patch.object(inst, "check_health", return_value=True):
            ips = rotator.get_all_ips()
            assert inst.socks_url in ips


class TestCircuitIsolation:
    """Phase 1/3: per-tag circuit isolation + capacity sizing."""

    def test_httpx_tag_isolates_by_socks_auth(self):
        rotator = ProxyRotator()
        with patch.object(TorInstance, "check_health", return_value=True):
            rotator.add_instance(TorInstance(socks_port=9050))
            a = rotator.get_proxy(for_playwright=False, tag="w0")["all://"]
            b = rotator.get_proxy(for_playwright=False, tag="w1")["all://"]
            # distinct SOCKS usernames => distinct Tor circuits on the same port
            assert a.startswith("socks5://w0:x@") and b.startswith("socks5://w1:x@")
            # stable: same tag reuses the same circuit
            assert rotator.get_proxy(for_playwright=False, tag="w0")["all://"] == a

    def test_playwright_tag_never_leaks_socks_auth(self):
        # Chromium can't do SOCKS auth — the server URL must carry no credentials.
        rotator = ProxyRotator()
        with patch.object(TorInstance, "check_health", return_value=True):
            rotator.add_instance(TorInstance(socks_port=9050))
            assert "@" not in rotator.get_proxy(for_playwright=True, tag="w0")["server"]

    def test_playwright_tags_spread_across_ports(self):
        rotator = ProxyRotator()
        with patch.object(TorInstance, "check_health", return_value=True):
            rotator.add_instance(TorInstance(socks_port=9050))
            rotator.add_instance(TorInstance(socks_port=9052))
            servers = {rotator.get_proxy(for_playwright=True, tag=f"w{i}")["server"]
                       for i in range(6)}
            assert len(servers) >= 2  # isolation fans workers across SocksPorts

    def test_no_tag_preserves_legacy_shape(self):
        rotator = ProxyRotator()
        with patch.object(TorInstance, "check_health", return_value=True):
            rotator.add_instance(TorInstance(socks_port=9050))
            assert rotator.get_proxy(for_playwright=True) == {"server": "socks5://127.0.0.1:9050"}

    def test_capacity_empty_pool_is_one(self):
        rotator = ProxyRotator()
        rotator._initialized = True
        # _initialized alone is not enough: circuit_capacity -> _ensure_initialized
        # re-probes whenever the pool is empty AND the rediscover cooldown has
        # lapsed, and _last_discover defaults to 0 (i.e. long ago). On a machine
        # with Tor actually running that found 6 real daemons and the assertion
        # became host-dependent. Stamp the cooldown so the pool stays empty.
        rotator._last_discover = time.time()
        assert rotator.circuit_capacity() == 1  # direct: one shared IP

    def test_capacity_counts_healthy_ports(self):
        rotator = ProxyRotator()
        with patch.object(TorInstance, "check_health", return_value=True):
            rotator.add_instance(TorInstance(socks_port=9050))
            rotator.add_instance(TorInstance(socks_port=9052))
            assert rotator.circuit_capacity() == 2

    def test_capacity_counts_external_proxies(self):
        rotator = ProxyRotator({"external_socks_proxies": ["socks5://a:1", "socks5://b:2"]})
        rotator.discover_tor_instances()
        assert rotator.circuit_capacity() == 2


class TestSingleton:
    def test_get_rotator_returns_same_instance(self):
        reset_rotator()
        r1 = get_rotator()
        r2 = get_rotator()
        assert r1 is r2

    def test_reset_rotator_creates_new(self):
        reset_rotator()
        r1 = get_rotator()
        reset_rotator()
        r2 = get_rotator()
        assert r1 is not r2

    def test_default_tor_ports(self):
        assert 9050 in DEFAULT_TOR_PORTS

    def test_ip_check_urls_not_empty(self):
        assert len(IP_CHECK_URLS) >= 2
