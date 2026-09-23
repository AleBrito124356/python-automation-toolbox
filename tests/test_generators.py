"""Generators: 11 Wi-Fi QR, 12 passwords, 13 QR codes."""

from __future__ import annotations

import math
import re
import string

import pytest
import qrcode
from PIL import Image

from conftest import load_script, offline_env


def png_matches_payload(png, payload: str, border: int = 2) -> bool:
    """Sample every module of the PNG and compare with the QR matrix for `payload`."""
    qr = qrcode.QRCode(border=border)
    qr.add_data(payload)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    with Image.open(png) as img:
        gray = img.convert("L")
        box = gray.size[0] // len(matrix)
        if gray.size != (box * len(matrix), box * len(matrix)):
            return False
        for y, row in enumerate(matrix):
            for x, dark in enumerate(row):
                pixel = gray.getpixel((x * box + box // 2, y * box + box // 2))
                if (pixel < 128) != dark:
                    return False
    return True


# --------------------------------------------------------------------------- 11 Wi-Fi QR

def test_wifi_payload_escaping():
    mod = load_script("11")
    assert mod.build_payload('Casa;Brito', 'p:a"ss\\', "WPA", True) == \
        'WIFI:T:WPA;S:Casa\\;Brito;P:p\\:a\\"ss\\\\;H:true;;'
    assert mod.build_payload("Guest", "ignored", "nopass", False) == "WIFI:T:nopass;S:Guest;;"


def test_wifi_png_encodes_exact_payload(tmp_path, run):
    result = run("11", "--ssid", "CasaBrito", "--password", "hunter2", "-o", tmp_path / "w.png")
    assert result.returncode == 0, result.stderr
    assert png_matches_payload(tmp_path / "w.png", "WIFI:T:WPA;S:CasaBrito;P:hunter2;;")


@pytest.mark.parametrize("encoding", ["cp1252", "ascii"])
def test_wifi_redirected_non_utf8_output_still_writes_png(tmp_path, run, encoding):
    """Audit repro: `11_wifi_qr.py ... > out.txt` crashed with UnicodeEncodeError, no PNG."""
    result = run("11", "--ssid", "Casa;Brito", "--password", 'p:a"ss', "-o", tmp_path / "wifi.png",
                 env=offline_env(PYTHONIOENCODING=encoding))
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "wifi.png").is_file()
    assert "##" in result.stdout          # plain fallback preview
    assert "Saved:" in result.stdout


def test_wifi_utf8_console_gets_block_preview(tmp_path, run):
    result = run("11", "--ssid", "Casa", "--password", "x", "-o", tmp_path / "w.png")
    assert "█" in result.stdout


# --------------------------------------------------------------------------- 12 passwords

def test_random_password_guarantees_every_class_and_entropy():
    mod = load_script("12")
    for _ in range(200):
        pwd, bits = mod.random_password(12, digits=True, symbols=True, upper=True)
        assert len(pwd) == 12
        assert any(c in string.ascii_lowercase for c in pwd)
        assert any(c in string.ascii_uppercase for c in pwd)
        assert any(c in string.digits for c in pwd)
        assert any(c in "!@#$%^&*-_=+?" for c in pwd)
    assert bits == pytest.approx(12 * math.log2(26 + 26 + 10 + 13))


def test_random_password_respects_exclusions_and_minimum():
    mod = load_script("12")
    pwd, bits = mod.random_password(30, digits=False, symbols=False, upper=False)
    assert set(pwd) <= set(string.ascii_lowercase)
    assert bits == pytest.approx(30 * math.log2(26))
    with pytest.raises(SystemExit):
        mod.random_password(3, digits=True, symbols=True, upper=True)


def test_pronounceable_password_shape_and_entropy():
    mod = load_script("12")
    pwd, bits = mod.pronounceable_password(5, 3)
    assert re.fullmatch(r"(?:[bcdfghjklmnpqrstvwzBCDFGHJKLMNPQRSTVWZ][aeiou]){5}-\d{3}", pwd)
    assert sum(c.isupper() for c in pwd) == 1
    expected = 5 * math.log2(19 * 5) + math.log2(5) + 3 * math.log2(10)
    assert bits == pytest.approx(expected)


def test_password_cli_prints_count_lines(run):
    result = run("12", "--length", "24", "--count", "3")
    lines = [line for line in result.stdout.splitlines() if "bits" in line]
    assert len(lines) == 3
    assert all(len(line.split()[0]) == 24 for line in lines)


# --------------------------------------------------------------------------- 13 QR codes

def test_vcard_rfc2426_escaping():
    """Audit repro: 'Acme, Inc.; Ventas' and 'Brito, Alejandro;Jr' were misparsed."""
    mod = load_script("13")
    card = mod.build_vcard("Brito, Alejandro;Jr", "+507 6000-0000", "ab@example.com",
                           "Acme, Inc.; Ventas", "https://example.com")
    lines = card.split("\r\n")
    assert lines[0] == "BEGIN:VCARD" and lines[-1] == "END:VCARD"
    assert "N:Brito;Alejandro\\;Jr;;;" in lines
    assert "FN:Brito\\, Alejandro\\;Jr" in lines
    assert "ORG:Acme\\, Inc.\\; Ventas" in lines
    assert "TEL;TYPE=CELL:+507 6000-0000" in lines
    plain = mod.build_vcard("Ana Maria Solis", None, None, None, None).split("\r\n")
    assert "N:Solis;Ana Maria;;;" in plain


@pytest.mark.parametrize("given, expected", [
    ("alejandrobrito.dev", "https://alejandrobrito.dev"),
    ("HTTPS://example.com/x", "HTTPS://example.com/x"),
    ("mailto:hola@example.com", "mailto:hola@example.com"),
    ("tel:+50760000000", "tel:+50760000000"),
])
def test_url_normalisation(given, expected):
    assert load_script("13").normalize_url(given) == expected


def test_qr_text_png_and_non_utf8_ascii_preview(tmp_path, run):
    """Audit repro: `13_qr_generator.py text hola --ascii -o t.png | tail` crashed, no PNG."""
    result = run("13", "text", "hola", "--ascii", "-o", tmp_path / "t.png",
                 env=offline_env(PYTHONIOENCODING="cp1252"))
    assert result.returncode == 0, result.stderr
    assert png_matches_payload(tmp_path / "t.png", "hola")
    assert "(21x21 modules)" in result.stdout


def test_qr_vcard_png_matches_payload(tmp_path, run):
    result = run("13", "vcard", "--name", "Ana Solis", "--org", "Acme, Inc.", "-o", tmp_path / "v.png")
    assert result.returncode == 0, result.stderr
    payload = load_script("13").build_vcard("Ana Solis", None, None, "Acme, Inc.", None)
    assert png_matches_payload(tmp_path / "v.png", payload)


def test_qr_too_much_data_is_a_clean_error(tmp_path, run):
    result = run("13", "text", "x" * 5000, "-o", tmp_path / "big.png")
    assert result.returncode == 1
    assert "Too much data" in result.stderr
