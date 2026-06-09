"""Tests for the LaunchAgent service helper (plist rendering only — no launchctl)."""

import plistlib

from photo_flow import service


def test_render_plist_is_valid_and_has_serve_args():
    xml = service.render_plist(host="127.0.0.1", port=7717)
    parsed = plistlib.loads(xml.encode("utf-8"))

    assert parsed["Label"] == "com.jkrumm.photoflow"
    args = parsed["ProgramArguments"]
    assert args[1:] == ["serve", "--host", "127.0.0.1", "--port", "7717"]
    assert args[0].endswith("photoflow")
    assert parsed["RunAtLoad"] is True
    assert parsed["KeepAlive"] is True
    # Localhost only — must never bind 0.0.0.0.
    assert "0.0.0.0" not in xml


def test_render_plist_honours_custom_port():
    parsed = plistlib.loads(service.render_plist(port=8000).encode("utf-8"))
    assert parsed["ProgramArguments"][-1] == "8000"
