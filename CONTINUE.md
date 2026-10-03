# CONTINUE — RadioConnect hand-off

Started 2026-10-03, at the user's request: merge Messenger and WalkieTalkie
into one app, following MFruit OS's app integration rules
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
| R0 | Seed from WalkieTalkie `e6a1127`; app id `radioconnect`; data in `WHISPLAY_OS_APP_DATA`; `RADIOCONNECT_` env prefix; native package files (manifest, run/install/test hooks); remove the standalone installers and provisioning (MFruit OS's `setup-radio.sh` owns them); README | DONE |
| R1 | Chat: a conversation view per contact, with Messenger's bubbles and delivery ticks over protocol v3 TEXT + ACK; keyboard compose; quick replies | DONE 2026-10-03: `app/chat.py`, Chats/conversation/Reply screens, ACK sent for texts to us, link `on_sent`/`send_ack`, inbox threads + status (atomic writes, limit 200); 14 tests; 650 pass. Pi: sideloaded 0.1.0 (install + test hooks ok), launched, shared ID 32478 and the Orange Pi contact adopted, Chats opened with keys, Esc exits (code 0). NOT verified on device: typing (mfruitctl key has no letters), a text over the air (no second radio) |
| R2 | SOS on protocol v3: one new type (0xF, the last free one) with a subtype byte (SOS, I'm OK); repeated until acknowledged, alarm, countdown; port Messenger's `emergency.py` logic and screens | TODO |
| R3 | Home: Talk · Chats · SOS · Pair · Settings · Status; footer hints from the one navigation table | TODO |
| R4 | Tests: controls through the real InputController, every screen inside the chrome, SOS and chat flows | TODO |
| R5 | check-app, sideload on the Pi, launch/exit, Fruit Store update/rollback/uninstall; record | TODO |
| R6 | Catalogue entry in MFruit OS (after the user creates and pushes the GitHub repo) | TODO |

## Facts

- The code is WalkieTalkie's: Python package `app/`, `python3 -m app.main`.
  It has 636 tests (WalkieTalkie's, minus the provisioning tests).
- The SX126X driver, voice (Codec2), pairing, crypto and the shared radio
  store come with it. Provisioning is MFruit OS's job
  (`mfruitos/hosts/lora`).
- WalkieTalkie's protocol uses 4 bits for the packet type, and 0x0–0xE are
  taken, so SOS has to share 0xF with a subtype.
- Messenger's chat history, SOS logic and screens are in
  `~/Messenger/messaging/{history,emergency}.py` and `controls/menus.py`. They
  are written against Messenger's own packet format and must be adapted, not
  copied as they are.

## Not done / not verified

- GitHub repository exists (`origin`); the user committed only the README so far.
  Logger names and the lock file are `radioconnect` now.
- `docs/walkietalkie-reference.md` describes WalkieTalkie. Update it as
  RadioConnect's behaviour diverges.
