"""OS integrations with fakes: 16 clipboard, 17 screenshots, 20 startup registry.

Nothing here reads the real clipboard, screen or registry.
"""

from __future__ import annotations

import json
import sys
import types
import warnings
import zlib
from pathlib import Path

import pytest

from conftest import load_script


def run_main(mod, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["script", *map(str, argv)])
    try:
        mod.main()
    except SystemExit as exc:
        return exc.code
    return 0


# --------------------------------------------------------------------------- 16 clipboard

@pytest.fixture
def fake_clipboard(monkeypatch):
    """pyperclip whose paste() walks through a scripted sequence of clipboard states."""
    module = types.ModuleType("pyperclip")

    class PyperclipException(RuntimeError):
        pass

    state = {"sequence": [], "i": 0}

    def paste():
        seq = state["sequence"]
        value = seq[min(state["i"], len(seq) - 1)]
        state["i"] += 1
        if isinstance(value, Exception):
            raise value
        return value

    module.PyperclipException = PyperclipException
    module.paste = paste
    monkeypatch.setitem(sys.modules, "pyperclip", module)
    return state, PyperclipException


def stop_after(monkeypatch, mod, ticks: int) -> None:
    """Replace time.sleep in the script: Ctrl+C after `ticks` polls."""
    count = {"n": 0}

    def fake_sleep(_seconds):
        count["n"] += 1
        if count["n"] > ticks:
            raise KeyboardInterrupt

    monkeypatch.setattr(mod.time, "sleep", fake_sleep)


def test_clipboard_history_dedupes_and_truncates(tmp_path, monkeypatch, capsys, fake_clipboard):
    state, busy = fake_clipboard
    state["sequence"] = ["already there", "first", "first", busy("locked"), "second",
                         "first", "   ", "x" * 50, "already there"]
    mod = load_script("16")
    stop_after(monkeypatch, mod, ticks=8)
    out = tmp_path / "hist.jsonl"
    assert run_main(mod, monkeypatch, "--out", out, "--max-chars", "10") == 0
    entries = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [e["text"] for e in entries] == ["first", "second", "x" * 10]
    assert entries[2]["truncated"] is True and entries[2]["chars"] == 50
    summary = capsys.readouterr().out
    assert "Captured:  3 entries" in summary
    assert "Skipped:   2 duplicates" in summary   # "first" again, "already there" again


def test_clipboard_allow_repeats(tmp_path, monkeypatch, fake_clipboard):
    state, _ = fake_clipboard
    state["sequence"] = ["", "a", "b", "a"]
    mod = load_script("16")
    stop_after(monkeypatch, mod, ticks=3)
    out = tmp_path / "h.jsonl"
    run_main(mod, monkeypatch, "--out", out, "--allow-repeats")
    assert [json.loads(line)["text"] for line in out.read_text().splitlines()] == ["a", "b", "a"]


def test_clipboard_unavailable_is_a_clean_exit(tmp_path, monkeypatch, fake_clipboard):
    state, busy = fake_clipboard
    state["sequence"] = [busy("no clipboard mechanism")]
    mod = load_script("16")
    code = run_main(mod, monkeypatch, "--out", tmp_path / "h.jsonl")
    assert "Clipboard unavailable" in str(code)


# --------------------------------------------------------------------------- 17 screenshots

def make_fake_mss(monkeypatch, *, modern: bool):
    grabs: list[dict] = []
    mss_mod = types.ModuleType("mss")
    tools = types.ModuleType("mss.tools")

    class Shot:
        def __init__(self, monitor):
            self.size = (monitor["width"], monitor["height"])
            self.rgb = b"\x10\x20\x30" * (self.size[0] * self.size[1])

    class FakeMSS:
        monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 1920, "top": 0, "width": 1920, "height": 1080},
        ]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def grab(self, monitor):
            grabs.append(dict(monitor))
            return Shot(monitor)

    def to_png(rgb, size, output):
        # A real (tiny) PNG so the test can check the file, not just its name.
        width, height = size
        raw = b"".join(b"\x00" + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))

        def chunk(kind, data):
            return (len(data).to_bytes(4, "big") + kind + data
                    + zlib.crc32(kind + data).to_bytes(4, "big"))
        ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
        Path(output).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                                 + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

    def deprecated_factory():
        warnings.warn("mss.mss is deprecated", DeprecationWarning, stacklevel=2)
        return FakeMSS()

    tools.to_png = to_png
    mss_mod.tools = tools
    mss_mod.mss = deprecated_factory
    if modern:
        mss_mod.MSS = FakeMSS
    monkeypatch.setitem(sys.modules, "mss", mss_mod)
    monkeypatch.setitem(sys.modules, "mss.tools", tools)
    return grabs


def test_screenshot_uses_modern_mss_api_without_warnings(tmp_path, monkeypatch, capsys):
    """Audit repro: mss 10 printed 'mss.mss is deprecated' on every run."""
    make_fake_mss(monkeypatch, modern=True)
    mod = load_script("17")
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert run_main(mod, monkeypatch, "--list") == 0
    out = capsys.readouterr().out
    assert "[2] monitor 2: 1920x1080 at (1920, 0)" in out
    assert not (tmp_path / "shots").exists()


def test_screenshot_falls_back_to_old_mss(tmp_path, monkeypatch):
    make_fake_mss(monkeypatch, modern=False)
    mod = load_script("17")
    with pytest.warns(DeprecationWarning):
        assert run_main(mod, monkeypatch, "--list") == 0


def test_screenshot_region_and_timelapse(tmp_path, monkeypatch, capsys):
    from PIL import Image
    grabs = make_fake_mss(monkeypatch, modern=True)
    mod = load_script("17")
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    out = tmp_path / "shots"
    assert run_main(mod, monkeypatch, "--region", "10", "20", "64", "32", "-o", out) == 0
    assert grabs[-1] == {"left": 10, "top": 20, "width": 64, "height": 32}
    shot = next(out.glob("shot_*.png"))
    with Image.open(shot) as img:
        assert img.size == (64, 32)

    assert run_main(mod, monkeypatch, "--monitor", "2", "--every", "1", "--count", "3",
                    "-o", tmp_path / "lapse") == 0
    assert len(list((tmp_path / "lapse").glob("shot_*.png"))) == 3, "same-second names stay unique"
    assert grabs[-1]["left"] == 1920
    assert "No monitor 7" in str(run_main(mod, monkeypatch, "--monitor", "7"))
    assert run_main(mod, monkeypatch, "--region", "0", "0", "0", "10") == 2


# --------------------------------------------------------------------------- 20 startup auditor

@pytest.fixture
def fake_registry(monkeypatch, tmp_path):
    winreg = types.ModuleType("winreg")
    winreg.HKEY_CURRENT_USER = "HKCU"
    winreg.HKEY_LOCAL_MACHINE = "HKLM"
    run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    data = {
        ("HKCU", run_key): [("OneDrive", r'"C:\OneDrive\OneDrive.exe" /background', 1),
                            ("Spotify", r"C:\Spotify\Spotify.exe --autostart", 1)],
        ("HKLM", run_key): PermissionError("denied"),
    }

    class Key:
        def __init__(self, values):
            self.values = values

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def open_key(hive, subkey):
        value = data.get((hive, subkey))
        if value is None:
            raise FileNotFoundError(subkey)
        if isinstance(value, Exception):
            raise value
        return Key(value)

    def enum_value(key, index):
        if index >= len(key.values):
            raise OSError("no more data")
        return key.values[index]

    winreg.OpenKey = open_key
    winreg.EnumValue = enum_value
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    monkeypatch.setattr(sys, "platform", "win32")

    appdata = tmp_path / "AppData"
    user_startup = appdata / "Microsoft/Windows/Start Menu/Programs/Startup"
    user_startup.mkdir(parents=True)
    (user_startup / "Notes.lnk").write_bytes(b"L\x00\x00\x00")
    (user_startup / "desktop.ini").write_text("[.ShellClassInfo]")
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("ProgramData", str(tmp_path / "ProgramData"))


def test_startup_auditor_json_report(fake_registry, monkeypatch, capsys):
    mod = load_script("20")
    assert run_main(mod, monkeypatch, "--json") == 0
    report = {loc["location"]: loc for loc in json.loads(capsys.readouterr().out)}
    assert [e["name"] for e in report["HKCU Run"]["entries"]] == ["OneDrive", "Spotify"]
    assert report["HKLM Run"]["entries"][0]["name"] == "(access denied)"
    assert report["HKCU RunOnce"]["entries"] == []
    assert [e["name"] for e in report["User Startup folder"]["entries"]] == ["Notes.lnk"]
    assert report["Common Startup folder"]["entries"] == []


def test_startup_auditor_text_report(fake_registry, monkeypatch, capsys):
    mod = load_script("20")
    run_main(mod, monkeypatch)
    out = capsys.readouterr().out
    assert "4 entries across 7 locations" in out
    assert "Spotify.exe --autostart" in out
    assert "(empty)" in out


def test_startup_auditor_refuses_non_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(SystemExit, match="only runs on Windows"):
        load_script("20")
