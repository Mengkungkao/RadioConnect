# RadioConnect

Voice, chat and an emergency call over LoRa, in one mFruit OS app.

RadioConnect joins the two radio apps for the Whisplay HAT and a Waveshare
SX1262 LoRa HAT: **WalkieTalkie** (push-to-talk voice, pairing, encryption,
privacy channels) and **Messenger** (text chat, keyboard typing, SOS). It
uses WalkieTalkie's radio protocol (version 3), so it works with radios still
running WalkieTalkie. Radios running the old Messenger use a different
protocol and need RadioConnect to talk to it.

> **Status: 0.6.0, in development.** Talk, Chats, pairing and unpairing,
> listening in the background, listen before talk, radios in range in the
> status bar.
> SOS is next ([CONTINUE.md](CONTINUE.md)). It is not a certified emergency
> service.

## Talk

**Home → Talk** lists *Everyone* and each paired radio, then **Replay last
voice**, **Conversations** and **Back**. You don't open a radio to talk to it:
moving the selection onto it chooses it, and the line at the top says who a
hold talks to.

| On Talk | Button | Keyboard |
|---|---|---|
| Choose who to talk to | tap / 2× onto their row | Down / Up |
| Talk to them | hold, talk, release | hold Space |
| Their conversation (texts, voice, replay one) | 3× on their row | Enter |
| Replay the last voice message | hold on **Replay last voice** | Enter |
| All conversations | hold on **Conversations** | Enter |
| Back | 4×, or hold on **Back** | Esc |

While you talk, the screen shows the recording, then the sending progress, and
returns to the list so you can replay or leave straight away.

**Pending, sending, sent.** While a message of yours is not yet on the air, a
floating pill says so on every screen: "pending: voice to Base · airtime in
0:42" while it waits for airtime, then "sending voice to Base · 3/20". A
message waits up to 10 minutes for airtime; it is never thrown away for it.
Only one that could not go out within that time is refused, with how long to
wait.

**Airtime and voice quality.** A radio may transmit only part of each hour.
For EU 868 that is the legal 1% (36 seconds). For AU915 and US915, which have
no such hourly cap, RadioConnect uses 10%. Voice quality follows the air
rate: Codec2 3200 at 9.6k, 1600 at the 2.4k AU915 setup, so sending takes
about as long as speaking. At 3200 over 2.4k, sending took 1.6 times longer
than speaking, which is why long messages ran out of airtime and the Orange
Pi seemed slow. Both can be changed: **Settings → Voice quality**, and
`duty_cycle_percent` in `config.yaml`.

**Did it arrive?** Each radio's row and each bubble in a conversation shows
what happened to your last message: **✓** sent on the air, **✓✓** the other
radio confirmed it arrived (a message also pops up: "✓✓ Base got your 3s
voice"), **✓ not confirmed** after a minute without that confirmation,
**not sent** if it never went out. Messages to *Everyone* are not confirmed:
every radio answering at once would collide. Old WalkieTalkie radios never
confirm.

## Pairing and unpairing

**Home → Pair devices** on both radios, then hold on the other radio's name
on one of them and accept the code on the other. If the answer is lost on the
air, the asking radio asks again and gets it, without a second question.

The status bar shows a small radio and how many paired radios are in range
right now: green, amber when every one of them is only weakly heard, a grey
**0** when none answers. Paired radios check on each other every two minutes
(`link_check_seconds`). When a radio connects, comes back or drops out, a
pop-up says which, and how many are in range with it ("Ridge connected · 2 in
range"). Before 0.6.0 that place held four signal bars for the last packet
heard; that signal is still on **Status** and on the range test.

**Settings → Paired radios** lists them. Hold on one to unpair it, then confirm.
That removes its keys, contact and name in every radio app on this device. Its
past messages stay in Chats' history. Unpair on the other radio too, or it will
keep sending messages this one can no longer read.

## Listening in the background

**Settings → Listen in background** (needs mFruit OS 1.4 with SDK 1.4.0 or
newer). When it is on, leaving RadioConnect (four clicks or Esc on its first
screen) gives the screen back to mFruit OS but keeps the radio listening:
messages and voice still arrive and are acknowledged, and you see them when
you open RadioConnect again from Home.

The screen then stays at full brightness until RadioConnect stops. On the
LoRa HAT with its stock jumpers the backlight pin is also the radio's M0, and
any dimming or screen-off leaves the radio deaf (measured on two boards: no
packet heard at 80% or 15%, all at 100%). The switch turns on both of mFruit
OS's *Keep running* and *Keep screen bright* for RadioConnect, which you also
find in **mFruit OS Settings → Apps → RadioConnect**. To stop listening, turn
the switch off, or stop the app there.

## Listen before talk

A radio cannot hear while it transmits, and two radios that transmit at once
usually both lose what they sent. So before each message RadioConnect waits
for a clear channel:

- **Another radio's message in progress.** Every packet says "fragment 2 of
  6", even one for somebody else, so the rest of that message is waited out.
- **Room to answer.** After a text, voice message, ping or call to one radio,
  that radio answers at once (✓✓, a pong). Every other radio, the sender
  included, leaves it time to do so. Answers themselves go first.
- **The channel's level,** when the radio module reports it (the E22 in the
  Waveshare HAT does, once mFruit OS's radio setup has run). This catches the
  first fragment of a message, before any of it has arrived.

After a busy channel a message waits a further random moment, so radios that
were all waiting do not start together. A message waits at most 45 s; an
unexplained signal holds it for at most 5 s. While a text or voice message
waits, the pill says **pending: … · channel busy**. **Status** shows the
channel's level (*noise*, in dBm) next to the last signal heard, and how many
messages waited.

It changes nothing on the air, so it works with every radio. Radios that do
not have it (WalkieTalkie, RadioConnect before 0.6.0) do not wait for you.
To turn it off, set `listen_before_talk: false` under `radio:` in
`config.yaml`.

## Chats

**Home → Chats** lists *Everyone*, then each paired radio, with the newest
conversation first and how many messages are unread. A conversation shows
texts and voice messages as bubbles, oldest at the top:

| In a conversation | Button | Keyboard |
|---|---|---|
| Send a voice message to this radio | hold, talk, release | hold Space |
| Write a text | — | type; Enter sends, Esc cancels, Backspace erases |
| Quick reply ("OK", "On my way", "Need help", …) | 3× | Enter on **Reply** |
| Play a voice message (received ones show who sent them) | select it, then 3× | select it, then Enter |
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

2. Install RadioConnect from **Fruit Store → RadioConnect → Install**, or
   `mfruitctl catalog radioconnect` over SSH. mFruit OS downloads the reviewed
   release pinned in its catalogue, checks its SHA-256, runs `install.sh`
   (checks only, no sudo) and `test.sh`, then adds RadioConnect to Home.

   A local copy works too: `mfruitctl sideload ~/RadioConnect`, or
   **Fruit Store → Local packages**.

**Fruit Store → RadioConnect** updates, rolls back, resets, uninstalls and
deletes it (also `mfruitctl rollback|reset|uninstall|delete radioconnect`).
Close RadioConnect first: mFruit OS refuses to install over an app that is
open. Only one radio app can use the radio at a time. Messenger and
WalkieTalkie can stay installed, but quit one before opening another.

RadioConnect needs nothing from them: the radio setup, the shared radio
identity and the SDK all come from mFruit OS. On its first start it copies
WalkieTalkie's messages (if WalkieTalkie was used on this device) into its own
chats, leaving WalkieTalkie's files untouched, so both old apps can be removed
afterwards. Messenger's history is not copied: its old Device IDs match no
contact. After **Reset app**, WalkieTalkie's messages are copied once more if
they are still on the device.

### Two radios that do not hear each other

Both radios must use the same frequency and air rate. **Pair** shows them
(for example `920 MHz · 2.4k air`), and so do Home and **Status**. "Radio not
set up" means mFruit OS's radio setup has not run on that device. It is then
using `config.yaml`'s 868 MHz at 9.6k, and a radio that was set up will not
hear it. Run the setup on that device too. Pairing then works across
privacy channels, and messages need the same channel on both.

## What it shares with the other radio apps

Pairing works across all of them. mFruit OS keeps one radio identity per
device in `~/.whisplay-os/shared/radio/`: the radio settings, the Device ID,
the pairing keys and the contact names. A radio paired in WalkieTalkie or
Messenger is already paired in RadioConnect.

RadioConnect keeps its own data (messages, settings changed on the device)
in its mFruit OS data folder, `~/.whisplay-os/apps/radioconnect/data/`.
Fruit Store's **Reset app** and **Delete data** act on that folder. They
never touch the shared radio store.

## Controls

The same as every mFruit app: **tap** next · **2×** previous · **hold, then
release** open · **4×** back. On talk screens, holding the button (or Space)
talks while held, and **3×** opens the selected row. Esc is back and Enter
opens. The full table, per screen, is in the
[WalkieTalkie reference](docs/walkietalkie-reference.md#using-it).

## Releasing

```bash
# set "version" in manifest.json, then:
tools/build-release.sh          # dist/radioconnect-<version>.tar.gz + SHA256SUMS
gh release create v<version> dist/radioconnect-<version>.tar.gz dist/SHA256SUMS
```

The tag must be the manifest version. The Fruit Store then offers it as an
update, verifies the checksum, and keeps the previous version for Roll back.
A failed smoke test (`test.sh`) keeps the version that was installed.

## Development

```bash
python3 -m pytest -q                      # tests (no hardware)
python3 tools/preview.py --out /tmp/rc    # every screen as PNG
python3 ~/MFruitOS/scripts/check-app.py . # mFruit OS package check
```

- App contract: [.claude/rules/mfruit-os-app.md](.claude/rules/mfruit-os-app.md),
  a byte-identical copy of mFruit OS `docs/apps/APP_CONTRACT.md`.
- `mfruit_sdk/` is vendored from mFruit OS. Never edit it here; refresh it
  with `~/MFruitOS/scripts/sdk-sync.sh .`.
- Data lives in `RADIOCONNECT_DATA_DIR`, else `WHISPLAY_OS_APP_DATA`, else
  `~/.radioconnect`. Environment overrides use the `RADIOCONNECT_` prefix
  (for example `RADIOCONNECT_LOG_LEVEL=DEBUG`).
- How the radio, voice and protocol work, range figures and troubleshooting:
  [docs/walkietalkie-reference.md](docs/walkietalkie-reference.md). Some of
  its install steps (`setup.sh`, the board installers, `provision_radio.py`)
  were replaced by mFruit OS's radio setup and are not in this repository.

## Licence and credits

As in WalkieTalkie; see the
[reference](docs/walkietalkie-reference.md#licence-and-credits).
