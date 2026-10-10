# CONTINUE — RadioConnect hand-off

Started 2026-10-03, at the user's request: merge Messenger and WalkieTalkie
into one app, following mFruit OS's app integration rules
(`.claude/rules/mfruit-os-app.md`).

## Decisions (by the user)

- Name **RadioConnect**, in `/home/meng/RadioConnect`.
- Protocol: **WalkieTalkie v3 plus SOS**. It works with WalkieTalkie radios;
  old Messenger radios need RadioConnect.
- **Keep Messenger and WalkieTalkie** installed for now. RadioConnect is a
  new app (`radioconnect`), not a replacement.

## Plan

| Step | Work | Status |
|---|---|---|
| R0 | Seed from WalkieTalkie `e6a1127`; app id `radioconnect`; data in `WHISPLAY_OS_APP_DATA`; `RADIOCONNECT_` env prefix; native package files (manifest, run/install/test hooks); remove the standalone installers and provisioning (mFruit OS's `setup-radio.sh` owns them); README | DONE |
| R1 | Chat: a conversation view per contact, with Messenger's bubbles and delivery ticks over protocol v3 TEXT + ACK; keyboard compose; quick replies | DONE 2026-10-03: `app/chat.py`, Chats/conversation/Reply screens, ACK sent for texts to us, link `on_sent`/`send_ack`, inbox threads + status (atomic writes, limit 200); 14 tests; 650 pass. Pi: sideloaded 0.1.0 (install + test hooks ok), launched, shared ID 32478 and the Orange Pi contact adopted, Chats opened with keys, Esc exits (code 0). NOT verified on device: typing (mfruitctl key has no letters), a text over the air (no second radio) |
| L1 | Standalone lifecycle on mFruit OS (user plans to remove Messenger and WalkieTalkie) | DONE 2026-10-03: 0.2.0; `tools/build-release.sh`; one-time WalkieTalkie message import; radio-not-set-up warnings and MHz/air on Pair/Home/Status. Pi: update 0.1.0→0.2.0, rollback, update again, failed update (broken test.sh) rolled back with data restored, reset, uninstall (data kept), reinstall (data back), uninstall+delete (all gone, shared radio store kept), fresh install; install over an open app refused. Orange Pi: update 0.1.0→0.2.0 |
| P2 | Pi and Orange Pi do not hear each other | FIXED: (1) the Pi radio was not set up (user ran setup-radio.sh; both 920 MHz/2.4k); (2) pairing was one-sided: the Orange Pi's single PAIR_ACCEPT was lost while the Pi beaconed, and the Pi's repeat request was ignored, so the Pi dropped the Orange Pi's messages. 0.3.1: requests repeat every 4 s, no beacons while waiting, an already-paired radio answers a repeat (regression test with negative control). Devices: re-paired (Orange Pi answered again automatically); text Pi→Orange Pi and Orange Pi→Pi both ✓✓ (driven with mfruitctl keys, screens captured from the app framebuffer). NOT verified: voice Orange Pi→Pi after the fix (mfruitctl cannot hold the button or Space) |
| U1 | Talk screen redesign (user) | DONE 0.3.0: Talk lists Everyone + paired radios + Replay last voice + Conversations + Back; moving chooses the target; hold talks in place (disc overlay, then back to the list); 3x/Enter opens the conversation; voice is acknowledged too (ACK), ✓/✓✓/not confirmed/not sent on rows and bubbles, arrival pop-up |
| U2 | Unpair (user) | DONE 0.3.2: Settings › Paired radios, hold → confirm; removes keys (shared), contact, shared name; target falls back to Everyone. Pi: stale 6235 unpaired on the device |
| U3 | User: long voice "duty cycle full", Orange Pi slow, sender names | DONE 0.4.0: duty cycle by band (eu868 1%, au915/us915 10%; config.yaml `auto`); codec by air rate (1600 at 2.4k; was 3200 = 1.6x slower than speech); messages wait up to 10 min for airtime instead of being refused; floating pill pending/sending on every screen and "pending" on the talking disc; sender name on received bubbles; config.yaml no longer persisted (so `auto` reaches existing installs; device settings are in data/). Installed on both: codec2=1600. NOT verified on device: a >7 s message end to end (needs a person to hold the button) |
| B1 | Listen in background (user, 2026-10-04): Settings switch; leaving releases the screen and keeps listening; mFruit OS SDK 1.4.0 `background` sets Keep running + Keep screen bright (the backlight pin is the radio's M0: dimming deafens it, measured 0/20 vs 40/40) | DONE 0.5.0: 5 new tests (negative control), settings-row tests updated, 680 pass; preview checked. DEVICE (Pi, 2026-10-04): switch on, leave, a quick reply from the Orange Pi received and acknowledged in the background 8 min later (✓✓), reopened the same process, switch off (mFruit OS record 2026-10-04-radio-over-the-air). Committed and pushed as `29694c4`; the mFruit OS Fruit Store list pins 0.5.0 since 2026-10-05 |
| R2 | SOS on protocol v3: one new type (0xF, the last free one) with a subtype byte (SOS, I'm OK); repeated until acknowledged, alarm, countdown; port Messenger's `emergency.py` logic and screens | TODO |
| R3 | Home: Talk · Chats · SOS · Pair · Settings · Status; footer hints from the one navigation table | TODO |
| R4 | Tests: controls through the real InputController, every screen inside the chrome, SOS and chat flows | TODO |
| R5 | check-app, sideload on the Pi, launch/exit, Fruit Store update/rollback/uninstall; record | TODO |
| R6 | Catalogue entry in mFruit OS | DONE: native entry since 0.4.0 (mFruit OS `3413c72`), 0.5.0 (`29694c4`) on 2026-10-05. mFruit OS downloads the Store list from GitHub and offers *Update to <version>* to devices with an older RadioConnect, so a new version needs a new `ref`/`sha256`/`version` in mFruit OS `config/catalog.json` (`tools/build-release.sh` is only for sideload archives) |

## Facts

- The code is WalkieTalkie's: Python package `app/`, `python3 -m app.main`.
  It has 636 tests (WalkieTalkie's, minus the provisioning tests).
- The SX126X driver, voice (Codec2), pairing, crypto and the shared radio
  store come with it. Provisioning is mFruit OS's job
  (`mfruitos/hosts/lora`).
- WalkieTalkie's protocol uses 4 bits for the packet type, and 0x0–0xE are
  taken, so SOS has to share 0xF with a subtype.
- Messenger's chat history, SOS logic and screens are in
  `~/Messenger/messaging/{history,emergency}.py` and `controls/menus.py`. They
  are written against Messenger's own packet format and must be adapted, not
  copied as they are.

## Not done / not verified

- GitHub repository: `origin`, `main` pushed through 0.5.0 (`29694c4`). No tags
  or GitHub releases: versions reach devices through the Fruit Store list.
  Logger names and the lock file are `radioconnect` now.
- `docs/walkietalkie-reference.md` describes WalkieTalkie. Update it as
  RadioConnect's behaviour diverges.
