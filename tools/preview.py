#!/usr/bin/env python3
"""Render every screen to PNG without a Pi, from sample data.

    python3 tools/preview.py                     # -> /tmp/radioconnect-preview
    python3 tools/preview.py --out DIR

Writes one PNG per screen and state, plus all-screens.png, a contact sheet.
On a machine without mFruit OS installed, set
MFRUIT_FONT_DIR=~/MFruitOS/assets/fonts to preview with mFruit OS's font.
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from app.store.inbox import Item  # noqa: E402
from app.store.roster import Entry  # noqa: E402
from app.ui import screens, theme  # noqa: E402
from app.ui.editors import ClockEditor, ConfirmEditor, DigitEditor  # noqa: E402
from app.chat import QUICK_REPLIES  # noqa: E402
from app.ui.screens import (CHAT, CHATS, CONTACTS, EDIT, HOME, INBOX, PAIR, RANGE,  # noqa: E402
                            RECORDING, REPLIES, SENDING, SETTINGS, START, STATUS, TALK,
                            ViewState)


def sample(**overrides) -> ViewState:
    state = ViewState(
        callsign="Rover", address=5, frequency_mhz=868, channel=3,
        home_items=[
            {"key": "start", "label": "Talk", "value": "now talking to Base"},
            {"key": "chats", "label": "Chats", "value": "2 new messages"},
            {"key": "receive", "label": "Receive", "value": "2 new  ·  14 in all"},
            {"key": "pair", "label": "Pair devices", "value": "3 paired  ·  Base in range"},
            {"key": "settings", "label": "Settings", "value": "name, ID, privacy channel"},
            {"key": "status", "label": "Status", "value": "radio, signal, audio and power"},
            {"key": "range", "label": "Range test", "value": "probe a paired radio"},
            {"key": "back", "label": "Back to mFruit OS"},
        ],
        start_items=[
            {"key": "to", "address": 0xFFFF, "label": "Everyone", "value": "you 4s ✓"},
            {"key": "to", "address": 1, "label": "Base",
             "value": "in range  ·  you 3s ✓✓"},
            {"key": "to", "address": 9, "label": "Hilltop",
             "value": "weak signal  ·  heard 6s · new"},
            {"key": "replay", "label": "Replay last voice", "value": "from Hilltop · 6s · 2m"},
            {"key": "chats", "label": "Conversations", "value": "2 new messages"},
            {"key": "back", "label": "Back"},
        ],
        settings_items=[
            {"key": "name", "label": "Name", "value": "Rover  ·  what other radios see"},
            {"key": "device_id", "label": "Device ID", "value": "5  ·  unique to this radio"},
            {"key": "channel", "label": "Privacy channel", "value": "3  ·  others are ignored"},
            {"key": "background", "label": "Listen in background",
             "value": "on  ·  listens after you leave"},
            {"key": "reset", "label": "Reset all data", "value": "3 message(s), keys",
             "destructive": True},
            {"key": "back", "label": "Back"},
        ],
        entries=[
            Entry("Base", 1, True, last_heard=1.0, last_rssi=-72),
            Entry("Hilltop", 9, False, last_heard=1.0, last_rssi=-104),
            Entry("Ridge", 12, True, last_heard=1.0, last_rssi=-92),
        ],
        inbox=[
            Item("a", "voice", 1, "Base", 1.0, -80, duration=4.2),
            Item("b", "text", 9, "Hilltop", 1.0, -104, text="on my way back to the car"),
            Item("c", "voice", 1, "Base", 1.0, None, duration=9.9, played=True),
        ],
        unread=2, target_name="Base", target_address=1, last_rssi=-88,
        duty_fraction=0.42, codec_name="700C", battery_present=True, radios_in_range=2,
        battery_percent=76.0, battery_summary="76%  ·  about 5 h", wifi_level=3,
        stats={"packets_tx": 12, "packets_rx": 34, "frames_dropped": 2, "air": "2.4k",
               "noise": -108, "waits": "waited 3x"},
        pair_found=[(1234, "jarvis", -72, False), (8, "hilltop", None, True)],
        pair_status="looking for radios",
        range_view={"name": "Base", "interval": 30, "air": "2.4k", "success": 0.83,
                    "window": (5, 6), "down": -84, "up": -91, "answered": 10, "sent": 12,
                    "marks": 2, "elapsed": 754, "last_result": "answered in 1.2 s"},
    )
    state.chats = [
        {"key": 0xFFFF, "label": "Everyone", "value": "You: back at camp by six"},
        {"key": 1, "label": "Base", "value": "2 new · on my way back to the car"},
        {"key": 9, "label": "Hilltop", "value": "voice 4s"},
        {"key": "back", "label": "Back"},
    ]
    state.chat_peer = (1, "Base")
    state.chat_items = CHAT_ITEMS
    state.chat_index = len(CHAT_ITEMS)
    state.replies = [{"key": t, "label": t} for t in QUICK_REPLIES] + [{"key": "back", "label": "Back"}]
    for key, value in overrides.items():
        setattr(state, key, value)
    return state


CHAT_ITEMS = [
    Item("m1", "text", 1, "Base", 1.0, -80, text="Are you near the trail head?", played=True),
    Item("m2", "text", 5, "Base", 2.0, text="Yes, ten minutes away", outgoing=True, dst=1,
         status="delivered"),
    Item("m3", "voice", 1, "Base", 3.0, -84, duration=4.2),
    Item("m4", "text", 5, "Base", 4.0, text="On my way", outgoing=True, dst=1, status="sent"),
]


SHOTS = [
    ("1-home", dict(screen=HOME)),
    ("2-home-armed", dict(screen=HOME, home_index=1, armed=True)),
    ("3-start", dict(screen=START, start_index=1)),
    ("3b-start-replay", dict(screen=START, start_index=3)),
    ("3c-start-talking", dict(screen=START, start_index=1, radio_state=RECORDING,
                              record_level=0.5, record_seconds=2.1)),
    ("3d-start-sending", dict(screen=START, start_index=1, radio_state=SENDING,
                              tx_sent=3, tx_total=7)),
    ("4-contacts", dict(screen=CONTACTS, selected_index=1)),
    ("5-talk", dict(screen=TALK)),
    ("6-talk-recording", dict(screen=TALK, radio_state=RECORDING, record_level=0.6,
                              record_seconds=3.4)),
    ("7-talk-sending", dict(screen=TALK, radio_state=SENDING, tx_sent=3, tx_total=8)),
    ("8-inbox", dict(screen=INBOX, inbox_index=1)),
    ("9-status", dict(screen=STATUS)),
    ("10-settings", dict(screen=SETTINGS, settings_index=3)),
    ("11-edit-id", dict(screen=EDIT, editor=DigitEditor(5, digits=5),
                        editor_title="DEVICE ID", editor_hint="must be unique on the channel")),
    ("12-edit-clock", dict(screen=EDIT, editor=ClockEditor(datetime.datetime(2026, 9, 29, 14, 30)),
                           editor_title="DATE & TIME")),
    ("13-confirm", dict(screen=EDIT, editor=ConfirmEditor("Erase everything?",
                                                          "messages, voice clips, paired\nradios, keys and settings"),
                        editor_title="RESET")),
    ("14-pair", dict(screen=PAIR)),
    ("15-range", dict(screen=RANGE)),
    ("16-home-status", dict(screen=HOME, home_index=4)),
    ("17-home-back", dict(screen=HOME, home_index=6)),
    ("18-start-back", dict(screen=START, start_index=5)),
    ("19-contacts-back", dict(screen=CONTACTS, contacts_back=True)),
    ("20-inbox-back", dict(screen=INBOX, inbox_back=True)),
    ("21-pair-back", dict(screen=PAIR, pair_back=True)),
    ("22-contacts-empty", dict(screen=CONTACTS, entries=[], contacts_back=True)),
    ("23-inbox-empty", dict(screen=INBOX, inbox=[], inbox_back=True, unread=0)),
    ("24-pair-empty", dict(screen=PAIR, pair_found=[], pair_back=True)),
    ("25-settings-back", dict(screen=SETTINGS, settings_index=5)),
    ("26-chats", dict(screen=CHATS, chats_index=1)),
    ("27-chat", dict(screen=CHAT)),
    ("28-chat-voice", dict(screen=CHAT, chat_index=2)),
    ("29-chat-typing", dict(screen=CHAT, compose="See you at the car park")),
    ("30-chat-empty", dict(screen=CHAT, chat_items=[], chat_index=0)),
    ("31-replies", dict(screen=REPLIES, replies_index=1)),
    ("32-chat-back", dict(screen=CHAT, chat_index=len(CHAT_ITEMS) + 1)),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", default="/tmp/radioconnect-preview")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    tiles = []
    for name, overrides in SHOTS:
        state = sample(**overrides)
        image = Image.new("RGB", (theme.SCREEN_WIDTH, theme.SCREEN_HEIGHT), theme.BG)
        screens.RENDERERS[state.screen](ImageDraw.Draw(image), state)
        image.save(out / f"{name}.png")
        tiles.append(image)
        print(f"  wrote {out / name}.png")

    columns = 5
    rows = (len(tiles) + columns - 1) // columns
    sheet = Image.new("RGB", (8 + columns * (theme.SCREEN_WIDTH + 8),
                              8 + rows * (theme.SCREEN_HEIGHT + 8)), (24, 26, 34))
    for index, tile in enumerate(tiles):
        row, column = divmod(index, columns)
        sheet.paste(tile, (8 + column * (theme.SCREEN_WIDTH + 8),
                           8 + row * (theme.SCREEN_HEIGHT + 8)))
    sheet.save(out / "all-screens.png")
    print(f"  wrote {out / 'all-screens.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
