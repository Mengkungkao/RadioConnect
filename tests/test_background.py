"""Leaving the app, and what keeps running when focus goes away.

Four clicks exits: the app is something you open when you want it, not a
service behind the desktop. Losing *focus* is different -- another app
taking the screen must not take the radio with it, because the process
is still alive and still listening.
"""

from __future__ import annotations

import pytest
from mfruit_sdk.input import BACK as QUAD  # four clicks, or Esc

from app.config.settings import Settings
from app.main import WalkieApp
from app.ui import navigation as nav
from app.ui.screens import HOME, SETTINGS, TALK, ViewState


class FakeInput:
    def __init__(self):
        self.resets = 0

    def reset(self):
        self.resets += 1


class FakeBoard:
    def __init__(self):
        self.foreground_ready = True


class FakeRecorder:
    available = True

    def __init__(self):
        self.armed = True
        self.recording = False

    def disarm(self):
        self.armed = False

    def arm(self):
        self.armed = True
        return True


class FakeLink:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class FakeDisplay:
    screen_off = False

    def poke(self): pass
    def invalidate(self): pass
    def restore_backlight(self): pass
    def set_led(self, *_a, **_k): pass


@pytest.fixture
def app():
    instance = WalkieApp.__new__(WalkieApp)
    instance.settings = Settings()
    instance.board = FakeBoard()
    instance.display = FakeDisplay()
    instance.recorder = FakeRecorder()
    instance.link = FakeLink()
    instance.state = ViewState(screen=TALK)
    instance._wake = type("Event", (), {"set": lambda self: None})()
    instance._closing = False
    instance.running = True
    instance.input = FakeInput()
    return instance


def test_four_clicks_goes_back_and_leaves_from_home():
    assert nav.route(HOME, QUAD) == nav.EXIT_APP
    assert nav.route(TALK, QUAD) == nav.GO_BACK
    assert nav.route(SETTINGS, QUAD) == nav.GO_BACK


def test_stopping_sets_the_loop_to_finish(app):
    app.stop("user")
    assert app.running is False
    assert app._closing is True


def test_losing_focus_does_not_stop_the_radio(app):
    """Another app taking the screen must not take the radio with it."""
    app._on_focus_revoked()
    assert app.link.stopped is False
    assert app.running is True
    assert app.board.foreground_ready is False


def test_losing_focus_forgets_input_in_progress(app):
    """Keys typed into whatever has the screen now must not reach the radio."""
    app._on_focus_revoked()
    assert app.input.resets == 1


def test_losing_focus_while_closing_is_ignored(app):
    """Focus is revoked as part of shutting down; do not chase it."""
    app._closing = True
    app._on_focus_revoked()
    assert app.board.foreground_ready is True


def test_the_microphone_is_disarmed_off_screen(app):
    """Nobody can press talk on a screen they cannot see."""
    app.board.foreground_ready = False
    app._follow_idle_with_the_microphone()
    assert app.recorder.armed is False


def test_the_microphone_re_arms_once_visible_again(app):
    app.board.foreground_ready = False
    app._follow_idle_with_the_microphone()
    app.board.foreground_ready = True
    app._follow_idle_with_the_microphone()
    assert app.recorder.armed is True


# --- Listen in background (mFruit OS Keep running + Keep screen bright) ----
from mfruit_sdk import background as mfruit_background  # noqa: E402
from app import main as main_module  # noqa: E402


class ReleasingBoard(FakeBoard):
    def __init__(self):
        super().__init__()
        self.released = 0

    def release_focus(self):
        self.released += 1


def test_leaving_with_keep_running_releases_the_screen_and_keeps_listening(app, monkeypatch):
    app.board = ReleasingBoard()
    monkeypatch.setattr(main_module.mfruit_background, "get",
                        lambda app_id="": mfruit_background.State(True, True))
    app._dispatch(nav.EXIT_APP)
    assert app.board.released == 1
    assert app.running is True and app._closing is False
    assert app.link.stopped is False
    assert app.board.foreground_ready is False
    assert app.state.screen == HOME          # opened again from Home: starts at Home
    assert app.input.resets == 1


def test_leaving_without_keep_running_exits_as_before(app, monkeypatch):
    app.board = ReleasingBoard()
    monkeypatch.setattr(main_module.mfruit_background, "get",
                        lambda app_id="": mfruit_background.State(False, False))
    app._dispatch(nav.EXIT_APP)
    assert app.running is False
    assert app.board.released == 0


def test_without_mfruit_os_leaving_exits(app, monkeypatch):
    monkeypatch.setattr(main_module.mfruit_background, "get", lambda app_id="": None)
    app._background = None
    app._dispatch(nav.EXIT_APP)
    assert app.running is False


def test_the_switch_sets_both_mfruit_os_switches(app, monkeypatch):
    calls = []

    def fake_set(keep_running=None, screen_bright=None, app_id=""):
        calls.append((keep_running, screen_bright))
        return mfruit_background.State(keep_running, screen_bright)
    monkeypatch.setattr(main_module.mfruit_background, "set", fake_set)
    app._refresh_settings = lambda: None
    app._background = mfruit_background.State(False, False)
    app._toggle_background()
    assert calls == [(True, True)]
    assert "listens after you leave" in app._background_summary()
    app._toggle_background()
    assert calls[-1] == (False, False)
    assert app._background_summary().startswith("off")


def test_the_switch_explains_an_older_mfruit_os(app, monkeypatch):
    monkeypatch.setattr(main_module.mfruit_background, "get", lambda app_id="": None)
    monkeypatch.setattr(main_module.mfruit_background, "set",
                        lambda **kwargs: pytest.fail("must not ask an mFruit OS that cannot answer"))
    app._refresh_settings = lambda: None
    app._background = None
    app._toggle_background()
    assert "mFruit OS" in app.state.banner
    assert app._background_summary() == "needs mFruit OS 1.4 or newer"
