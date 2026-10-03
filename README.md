# RadioConnect

Voice, chat and an emergency call over LoRa, in one MFruit OS app.

RadioConnect joins the two radio apps for the Whisplay HAT and a Waveshare
SX1262 LoRa HAT: **WalkieTalkie** (push-to-talk voice, pairing, encryption,
privacy channels) and **Messenger** (text chat, keyboard typing, SOS). It
uses WalkieTalkie's radio protocol (version 3), so it works with radios still
running WalkieTalkie. Radios running the old Messenger use a different
protocol and need RadioConnect to talk to it.

> **Status: 0.1.0, in development.** WalkieTalkie's voice, pairing and
> settings, plus **Chats**. SOS is next ([CONTINUE.md](CONTINUE.md)). It is
> not a certified emergency service.

## Chats

**Home → Chats** lists *Everyone*, then each paired radio, with the newest
conversation first and how many messages are unread. A conversation shows
texts and voice messages as bubbles, oldest at the top:

| In a conversation | Button | Keyboard |
|---|---|---|
| Send a voice message to this radio | hold, talk, release | hold Space |
| Write a text | — | type; Enter sends, Esc cancels, Backspace erases |
| Quick reply ("OK", "On my way", "Need help", …) | 3× | Enter on **Reply** |
| Play a voice message | select it, then 3× | select it, then Enter |
| Move between messages | tap / 2× | Down / Up |
| Back to Chats | 4× | Esc |

A text to one radio shows ✓ once it is on the air and ✓✓ once that radio
confirms it arrived. WalkieTalkie radios do not confirm, so texts to them
stop at ✓, and so do texts to *Everyone*. A text that could not go out says
**not sent**.

## Install

1. Once per device, set up the radio over SSH. This installs Codec2 and the
   Python packages, frees the serial port and sets the module's frequency.
   It asks for your sudo password and may reboot:

   ```bash
   bash ~/.whisplay-os/system/current/scripts/setup-radio.sh
   ```

2. Install RadioConnect from a local copy of this folder:

   ```bash
   mfruitctl sideload ~/RadioConnect
   ```

   Or use **Fruit Store → Local packages**. MFruit OS checks the package,
   runs `install.sh` (checks only, no sudo) and `test.sh`, then adds
   RadioConnect to Home.

**Fruit Store → RadioConnect** updates, rolls back, resets, uninstalls and
deletes it. Only one radio app can use the radio at a time. Messenger and
WalkieTalkie can stay installed, but quit one before opening another.

## What it shares with the other radio apps

Pairing works across all of them. MFruit OS keeps one radio identity per
device in `~/.whisplay-os/shared/radio/`: the radio settings, the Device ID,
the pairing keys and the contact names. A radio paired in WalkieTalkie or
Messenger is already paired in RadioConnect.

RadioConnect keeps its own data (messages, settings changed on the device)
in its MFruit OS data folder, `~/.whisplay-os/apps/radioconnect/data/`.
Fruit Store's **Reset app** and **Delete data** act on that folder. They
never touch the shared radio store.

## Controls

The same as every MFruit app: **tap** next · **2×** previous · **hold, then
release** open · **4×** back. On talk screens, holding the button (or Space)
talks while held, and **3×** opens the selected row. Esc is back and Enter
opens. The full table, per screen, is in the
[WalkieTalkie reference](docs/walkietalkie-reference.md#using-it).

## Development

```bash
python3 -m pytest -q                      # tests (no hardware)
python3 tools/preview.py --out /tmp/rc    # every screen as PNG
python3 ~/MFruitOS/scripts/check-app.py . # MFruit OS package check
```

- App contract: [.claude/rules/mfruit-os-app.md](.claude/rules/mfruit-os-app.md),
  a byte-identical copy of MFruit OS `docs/apps/APP_CONTRACT.md`.
- `mfruit_sdk/` is vendored from MFruit OS. Never edit it here; refresh it
  with `~/MFruitOS/scripts/sdk-sync.sh .`.
- Data lives in `RADIOCONNECT_DATA_DIR`, else `WHISPLAY_OS_APP_DATA`, else
  `~/.radioconnect`. Environment overrides use the `RADIOCONNECT_` prefix
  (for example `RADIOCONNECT_LOG_LEVEL=DEBUG`).
- How the radio, voice and protocol work, range figures and troubleshooting:
  [docs/walkietalkie-reference.md](docs/walkietalkie-reference.md). Some of
  its install steps (`setup.sh`, the board installers, `provision_radio.py`)
  were replaced by MFruit OS's radio setup and are not in this repository.

## Licence and credits

As in WalkieTalkie; see the
[reference](docs/walkietalkie-reference.md#licence-and-credits).
