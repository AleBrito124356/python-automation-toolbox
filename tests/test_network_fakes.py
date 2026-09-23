"""Networked tools, fully offline: 14 uptime (local server), 15 TTS (fake edge_tts),
18 weather and 19 currency (fixture JSON through a fake urlopen)."""

from __future__ import annotations

import asyncio
import io
import json
import subprocess
import sys
import time
import types
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import load_script, offline_env, script_path


# --------------------------------------------------------------------------- 14 uptime

def write_sites(path: Path, base: str, **site_options) -> Path:
    site = {"name": "Shop", "url": f"{base}/site", **site_options}
    import yaml
    path.write_text(yaml.safe_dump({"sites": [site]}), encoding="utf-8")
    return path


def uptime(run, config, hook=None, *extra):
    env = offline_env(**({"UPTIME_WEBHOOK_URL": hook} if hook else {}))
    return run("14", config, "--timeout", "5", "--retry-delay", "0", *extra, env=env)


def test_uptime_all_up_exit_zero_and_state_file(tmp_path, run, http_server):
    config = write_sites(tmp_path / "sites.yaml", http_server.base, expect_text="operational")
    result = uptime(run, config, f"{http_server.base}/hook")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "UP" in result.stdout
    state = json.loads((tmp_path / "sites.state.json").read_text())
    assert state["Shop"]["state"] == "UP"
    assert http_server.hook_posts == [], "no alert when nothing is wrong"


def test_uptime_outage_alerts_once_then_recovers_once(tmp_path, run, http_server):
    config = write_sites(tmp_path / "sites.yaml", http_server.base)
    hook = f"{http_server.base}/hook"
    http_server.status = 503
    for _ in range(3):                                    # three cron runs, same outage
        result = uptime(run, config, hook)
        assert result.returncode == 1
        assert "HTTP 503, expected 200" in result.stdout
    assert len(http_server.hook_posts) == 1
    assert http_server.hook_posts[0]["text"].startswith("Uptime alert - DOWN Shop")
    assert http_server.hook_posts[0]["content"] == http_server.hook_posts[0]["text"]

    http_server.status = 200
    recovered = uptime(run, config, hook)
    assert recovered.returncode == 0
    assert len(http_server.hook_posts) == 2
    assert "RECOVERED Shop after" in http_server.hook_posts[1]["text"]
    uptime(run, config, hook)
    assert len(http_server.hook_posts) == 2, "no more alerts once it is back"


def test_uptime_failed_webhook_is_reported_retried_and_kept_pending(tmp_path, run, http_server):
    """Audit repro: 'Webhook alert sent' was printed although the webhook answered 404."""
    config = write_sites(tmp_path / "sites.yaml", http_server.base)
    hook = f"{http_server.base}/hook"
    http_server.status = 500
    http_server.hook_status = 404
    first = uptime(run, config, hook)
    assert "Webhook failed: HTTP 404" in first.stdout
    assert "alert sent" not in first.stdout
    assert len(http_server.hook_posts) == 2, "one retry"

    http_server.hook_status = 204
    second = uptime(run, config, hook)
    assert "Webhook alert sent" in second.stdout
    assert len(http_server.hook_posts) == 3, "the undelivered DOWN alert is sent on the next run"
    uptime(run, config, hook)
    assert len(http_server.hook_posts) == 3


def test_uptime_expect_text_and_latency(tmp_path, run, http_server):
    config = write_sites(tmp_path / "t.yaml", http_server.base, expect_text="operational")
    http_server.body = "<h1>Maintenance</h1>"
    result = uptime(run, config)
    assert result.returncode == 1
    assert "DOWN (expected text 'operational' not found in the page)" in result.stdout

    slow = write_sites(tmp_path / "s.yaml", http_server.base, max_latency_ms=100)
    http_server.body = "ok"
    http_server.delay = 0.4
    result = uptime(run, slow, None, "--json")
    report = json.loads(result.stdout)
    assert report["results"][0]["state"] == "SLOW"
    assert "limit" in report["results"][0]["error"]
    assert report["events"][0]["kind"] == "SLOW"
    assert result.returncode == 1


def test_uptime_connection_refused_is_down(tmp_path, run):
    config = write_sites(tmp_path / "c.yaml", "http://127.0.0.1:9")  # discard port: nothing listens
    result = uptime(run, config, None, "--json")
    report = json.loads(result.stdout)
    assert report["results"][0]["state"] == "DOWN"
    assert report["results"][0]["status"] is None
    assert "Connect" in report["results"][0]["error"]


@pytest.mark.parametrize("body, message", [
    ("sites: []\n", "no 'sites' list"),
    ("sites:\n  - name: a\n", "missing 'url'"),
    ("sites:\n  - url: http://a\n  - url: http://a\n", "names must be unique"),
    ("sites:\n  - url: http://a\n    expect_status: ok\n", "expect_status"),
    ("sites: [unclosed\n", "not valid YAML"),
])
def test_uptime_config_errors_exit_2(tmp_path, run, body, message):
    config = tmp_path / "bad.yaml"
    config.write_text(body, encoding="utf-8")
    result = run("14", config)
    assert result.returncode == 2
    assert message in result.stderr


def test_uptime_example_config_is_valid():
    from conftest import REPO
    config = load_script("14").load_config(REPO / "examples" / "sites.example.yaml")
    by_name = {s["name"]: s for s in config["sites"]}
    assert by_name["API health"]["expect_text"]
    assert by_name["Shop checkout"]["expect_status"] == [200, 302]
    assert by_name["Shop checkout"]["max_latency_ms"] == 1500


def test_uptime_alert_every_and_duration_logic():
    mod = load_script("14")
    t0 = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
    down = [{"name": "A", "url": "u", "state": "DOWN", "status": 503, "latency_ms": 5,
             "error": "HTTP 503, expected 200"}]
    up = [{**down[0], "state": "UP", "status": 200, "error": None}]

    events, state = mod.evaluate(down, {}, t0, alert_every_min=30)
    assert [e["kind"] for e in events] == ["DOWN"]
    mod.mark_notified(state, events, t0)
    events, state = mod.evaluate(down, state, t0 + timedelta(minutes=10), 30)
    assert events == []
    events, state = mod.evaluate(down, state, t0 + timedelta(minutes=31), 30)
    assert [e["kind"] for e in events] == ["STILL DOWN"]
    mod.mark_notified(state, events, t0 + timedelta(minutes=31))
    events, state = mod.evaluate(up, state, t0 + timedelta(minutes=45), 30)
    assert events[0]["kind"] == "RECOVERED"
    assert events[0]["duration_s"] == 45 * 60
    assert "after 45m 00s" in mod.describe(events[0])


def test_uptime_loop_mode_flushes_and_does_not_spam(tmp_path, http_server):
    """Audit repro: --loop re-sent the same alert every cycle and buffered its output."""
    config = write_sites(tmp_path / "sites.yaml", http_server.base)
    http_server.status = 503
    log = tmp_path / "loop.txt"
    env = offline_env(UPTIME_WEBHOOK_URL=f"{http_server.base}/hook")
    env.pop("PYTHONIOENCODING")
    with log.open("w") as out:
        proc = subprocess.Popen([sys.executable, str(script_path("14")), str(config),
                                 "--loop", "0.3", "--json", "--timeout", "5"],
                                stdout=out, stderr=subprocess.STDOUT, env=env)
        try:
            deadline = time.time() + 20
            while time.time() < deadline:
                if len(log.read_text().splitlines()) >= 4:
                    break
                time.sleep(0.2)
            lines = log.read_text().splitlines()   # read while the process is still running
        finally:
            proc.kill()
            proc.wait()
    cycles = [json.loads(line) for line in lines if line.startswith("{")]
    assert len(cycles) >= 4, lines
    assert all(c["results"][0]["state"] == "DOWN" for c in cycles)
    assert len(http_server.hook_posts) == 1


# --------------------------------------------------------------------------- 15 text to speech

@pytest.fixture
def fake_edge_tts(monkeypatch):
    calls: list[dict] = []
    module = types.ModuleType("edge_tts")

    async def list_voices():
        return [
            {"ShortName": "es-MX-DaliaNeural", "Gender": "Female", "Locale": "es-MX"},
            {"ShortName": "es-ES-AlvaroNeural", "Gender": "Male", "Locale": "es-ES"},
            {"ShortName": "en-US-AriaNeural", "Gender": "Female", "Locale": "en-US"},
        ]

    class Communicate:
        def __init__(self, text, voice, **kwargs):
            calls.append({"text": text, "voice": voice, **kwargs})

        async def save(self, path):
            Path(path).write_bytes(b"ID3fake-mp3")

    module.list_voices = list_voices
    module.Communicate = Communicate
    monkeypatch.setitem(sys.modules, "edge_tts", module)
    return calls


def run_main(mod, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["script", *map(str, argv)])
    try:
        mod.main()
    except SystemExit as exc:
        return exc.code
    return 0


def test_tts_synthesizes_file_with_rate_and_pitch(tmp_path, monkeypatch, capsys, fake_edge_tts):
    mod = load_script("15")
    source = tmp_path / "articulo.txt"
    source.write_text("  Hola desde Panamá  \n", encoding="utf-8")
    out = tmp_path / "a.mp3"
    code = run_main(mod, monkeypatch, "--file", source, "--voice", "es-MX-DaliaNeural",
                    "--rate", "+10%", "--pitch", "-5Hz", "-o", out)
    assert code == 0
    assert out.read_bytes().startswith(b"ID3")
    assert fake_edge_tts == [{"text": "Hola desde Panamá", "voice": "es-MX-DaliaNeural",
                              "rate": "+10%", "pitch": "-5Hz"}]


def test_tts_voice_listing_filters_by_locale(monkeypatch, capsys, fake_edge_tts):
    mod = load_script("15")
    run_main(mod, monkeypatch, "--list-voices", "--lang", "es")
    out = capsys.readouterr().out
    assert "es-MX-DaliaNeural" in out and "es-ES-AlvaroNeural" in out
    assert "en-US-AriaNeural" not in out
    assert "2 voice(s)" in out


def test_tts_empty_text_is_rejected(tmp_path, monkeypatch, fake_edge_tts):
    mod = load_script("15")
    empty = tmp_path / "e.txt"
    empty.write_text("   \n", encoding="utf-8")
    assert "empty" in str(run_main(mod, monkeypatch, "--file", empty))
    assert fake_edge_tts == []


# --------------------------------------------------------------------------- fake urlopen

class FakeWeb:
    """Stand-in for urllib.request.urlopen that serves fixture JSON by URL prefix."""

    def __init__(self):
        self.routes: list[tuple[str, object]] = []
        self.requests: list[str] = []

    def add(self, prefix: str, response) -> None:
        self.routes.append((prefix, response))

    def __call__(self, request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        self.requests.append(url)
        for prefix, response in self.routes:
            if url.startswith(prefix):
                if isinstance(response, BaseException):
                    raise response
                if isinstance(response, bytes):
                    return io.BytesIO(response)
                return io.BytesIO(json.dumps(response).encode("utf-8"))
        raise urllib.error.URLError("no route in FakeWeb")


@pytest.fixture
def fake_web(monkeypatch):
    web = FakeWeb()
    monkeypatch.setattr(urllib.request, "urlopen", web)
    return web


def http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "error", {}, io.BytesIO(b'{"message":"not found"}'))


# --------------------------------------------------------------------------- 18 weather

def test_weather_table_from_fixtures(fake_web, fixture_json, monkeypatch, capsys):
    mod = load_script("18")
    fake_web.add(mod.GEO_URL, fixture_json("open_meteo_geocoding.json"))
    fake_web.add(mod.FORECAST_URL, fixture_json("open_meteo_forecast.json"))
    assert run_main(mod, monkeypatch, "Panama City", "--days", "3") == 0
    out = capsys.readouterr().out
    assert "Panama City, Panama  (8.99, -79.52)" in out
    assert "Now: 29C, Light showers, humidity 78%, wind 10 km/h" in out
    assert "2026-09-24" in out and "Thunderstorm" in out
    assert out.splitlines()[-3].rstrip().endswith("--  Overcast")   # null rain probability
    query = urllib.parse.parse_qs(urllib.parse.urlparse(fake_web.requests[1]).query)
    assert query["forecast_days"] == ["3"] and query["temperature_unit"] == ["celsius"]


def test_weather_fahrenheit_and_errors(fake_web, fixture_json, monkeypatch, capsys):
    mod = load_script("18")
    fake_web.add(mod.GEO_URL, fixture_json("open_meteo_geocoding.json"))
    fake_web.add(mod.FORECAST_URL, fixture_json("open_meteo_forecast.json"))
    run_main(mod, monkeypatch, "Panama City", "--fahrenheit")
    assert "temperature_unit=fahrenheit" in fake_web.requests[1]

    empty = FakeWeb()
    empty.add(mod.GEO_URL, {"generationtime_ms": 0.1})
    monkeypatch.setattr(urllib.request, "urlopen", empty)
    assert "City not found" in str(run_main(mod, monkeypatch, "Atlantis"))

    down = FakeWeb()
    down.add(mod.GEO_URL, urllib.error.URLError("no route to host"))
    monkeypatch.setattr(urllib.request, "urlopen", down)
    assert "Network error" in str(run_main(mod, monkeypatch, "Berlin"))

    portal = FakeWeb()
    portal.add(mod.GEO_URL, b"<html>Sign in to Wi-Fi</html>")
    monkeypatch.setattr(urllib.request, "urlopen", portal)
    assert "not JSON" in str(run_main(mod, monkeypatch, "Berlin"))


# --------------------------------------------------------------------------- 19 currency

@pytest.fixture
def currency(tmp_path, monkeypatch):
    mod = load_script("19")
    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path / "cache")
    return mod


def test_currency_online_converts_and_primes_cache(currency, fake_web, fixture_json,
                                                   monkeypatch, capsys):
    fake_web.add(f"{currency.API_BASE}/latest?from=USD", fixture_json("frankfurter_latest_USD.json"))
    assert run_main(currency, monkeypatch, "100", "usd", "eur") == 0
    out = capsys.readouterr().out
    assert "100.00 USD = 91.85 EUR" in out
    assert "(ECB date 2026-09-22)" in out
    assert (currency.CACHE_DIR / "rates_USD.json").is_file()


def test_currency_unknown_code_is_not_reported_as_offline(currency, fake_web, monkeypatch):
    """Audit repro: HTTP 404 for an unknown currency said 'No network and no cached rates'."""
    fake_web.add(f"{currency.API_BASE}/latest?from=XYZ",
                 http_error(f"{currency.API_BASE}/latest?from=XYZ", 404))
    message = str(run_main(currency, monkeypatch, "100", "XYZ", "EUR"))
    assert "does not know 'XYZ'" in message
    assert "No network" not in message


def test_currency_offline_uses_any_cached_base_for_cross_rates(currency, fake_web, fixture_json,
                                                               monkeypatch, capsys):
    fake_web.add(f"{currency.API_BASE}/latest?from=USD", fixture_json("frankfurter_latest_USD.json"))
    run_main(currency, monkeypatch, "1", "USD", "EUR")          # primes rates_USD.json
    capsys.readouterr()

    offline = FakeWeb()                                          # now the network is gone
    offline.add("https://", urllib.error.URLError("getaddrinfo failed"))
    monkeypatch.setattr(urllib.request, "urlopen", offline)
    assert run_main(currency, monkeypatch, "100", "EUR", "GBP") == 0
    captured = capsys.readouterr()
    expected = 100 * 0.7851 / 0.9185
    assert f"100.00 EUR = {expected:,.2f} GBP" in captured.out
    assert "cross rate via USD" in captured.out
    assert "offline" in captured.err

    assert run_main(currency, monkeypatch, "10", "EUR", "USD") == 0
    assert f"10.00 EUR = {10 / 0.9185:,.2f} USD" in capsys.readouterr().out


def test_currency_server_error_falls_back_but_no_cache_explains(currency, fake_web, monkeypatch):
    fake_web.add("https://", http_error("https://api.frankfurter.app/latest", 503))
    message = str(run_main(currency, monkeypatch, "5", "USD", "JPY"))
    assert "No network (service error HTTP 503)" in message
    assert "USD -> JPY" in message


def test_currency_validation_and_pab_alias(currency, fake_web, fixture_json, monkeypatch, capsys):
    assert "not a currency code" in str(run_main(currency, monkeypatch, "5", "US", "EUR"))
    assert fake_web.requests == [], "invalid codes never hit the network"
    assert run_main(currency, monkeypatch, "2500", "PAB", "USD") == 0
    assert "2,500.00 PAB = 2,500.00 USD  (PAB pegged 1:1 to USD)" in capsys.readouterr().out
    fake_web.add(f"{currency.API_BASE}/latest?from=USD", fixture_json("frankfurter_latest_USD.json"))
    run_main(currency, monkeypatch, "10", "PAB", "MXN")
    assert "10.00 PAB = 184.20 MXN" in capsys.readouterr().out


def test_currency_list_online_then_offline(currency, fake_web, fixture_json, monkeypatch, capsys):
    fake_web.add(f"{currency.API_BASE}/currencies", fixture_json("frankfurter_currencies.json"))
    run_main(currency, monkeypatch, "--list")
    assert "MXN  Mexican Peso" in capsys.readouterr().out
    offline = FakeWeb()
    offline.add("https://", TimeoutError("timed out"))
    monkeypatch.setattr(urllib.request, "urlopen", offline)
    run_main(currency, monkeypatch, "--list")
    captured = capsys.readouterr()
    assert "5 currencies" in captured.out and "offline" in captured.err


def test_asyncio_still_works_under_the_network_guard():
    """The guard must allow the loopback socketpair asyncio uses internally."""
    async def nothing():
        return 42
    assert asyncio.run(nothing()) == 42
