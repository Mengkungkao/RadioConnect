"""Finding libcodec2: a library too old to bind means "no voice", not a crash.

Debian 11 and Ubuntu 20.04 ship libcodec2 0.9, which ctypes.util finds but
which lacks codec2_bytes_per_frame. Loading it used to raise AttributeError out
of codec2.available(), so the app failed where it should run without voice.
"""

from __future__ import annotations

import pytest

from app.audio import codec2


class OldCodec2:
    """A libcodec2 0.9 as ctypes sees it: every function but one."""

    class Function:
        argtypes = None
        restype = None

    def __getattr__(self, name):
        if name == "codec2_bytes_per_frame":
            raise AttributeError(f"undefined symbol: {name}")
        function = OldCodec2.Function()
        setattr(self, name, function)
        return function


@pytest.fixture
def only_an_old_library(monkeypatch):
    monkeypatch.setattr(codec2, "_lib", None)
    monkeypatch.setattr(codec2.ctypes.util, "find_library", lambda name: "libcodec2.so.0.9")

    def cdll(name):
        if name == "libcodec2.so.0.9":
            return OldCodec2()
        raise OSError(f"{name}: cannot open shared object file")

    monkeypatch.setattr(codec2.ctypes, "CDLL", cdll)


def test_a_too_old_library_means_no_voice_not_a_crash(only_an_old_library):
    assert codec2.available() is False
    with pytest.raises(codec2.Codec2Unavailable, match="too old .*libcodec2.so.0.9"):
        codec2._load()


def test_no_library_at_all_says_how_to_install_one(monkeypatch):
    monkeypatch.setattr(codec2, "_lib", None)
    monkeypatch.setattr(codec2.ctypes.util, "find_library", lambda name: None)

    def cdll(name):
        raise OSError(f"{name}: cannot open shared object file")

    monkeypatch.setattr(codec2.ctypes, "CDLL", cdll)
    assert codec2.available() is False
    with pytest.raises(codec2.Codec2Unavailable, match="apt install libcodec2"):
        codec2._load()
