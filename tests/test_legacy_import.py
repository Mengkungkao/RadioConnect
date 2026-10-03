"""WalkieTalkie's messages are copied into RadioConnect once, never moved."""

import json

from app.store import legacy_import
from app.store.inbox import Inbox


def walkie(tmp_path, items, clip=b"c2"):
    source = tmp_path / "walkie"
    (source / "voice").mkdir(parents=True)
    (source / "inbox.json").write_text(json.dumps(items))
    (source / "voice" / "v1.c2").write_bytes(clip)
    return source


ITEMS = [{"id": "v1", "kind": "voice", "src": 9, "peer_name": "Base", "received_at": 1.0,
          "voice_file": "v1.c2", "duration": 2.0},
         {"id": "t1", "kind": "text", "src": 5, "peer_name": "Base", "received_at": 2.0,
          "text": "hi", "outgoing": True, "dst": 9, "status": "sending"}]


def test_messages_and_voice_are_copied_once_and_the_originals_kept(tmp_path):
    source = walkie(tmp_path, ITEMS)
    data = tmp_path / "data"
    data.mkdir()
    assert legacy_import.import_walkietalkie(data, source) == 2
    inbox = Inbox(data)
    assert [i.id for i in inbox.items] == ["v1", "t1"]
    assert inbox.voice_bytes(inbox.items[0]) == b"c2"
    assert inbox.items[1].status == "failed", "never sent"
    assert (source / "inbox.json").exists() and (source / "voice" / "v1.c2").exists()
    (source / "inbox.json").write_text(json.dumps(ITEMS + ITEMS))
    assert legacy_import.import_walkietalkie(data, source) == 0, "only once"


def test_an_inbox_radioconnect_already_has_is_never_replaced(tmp_path):
    source = walkie(tmp_path, ITEMS)
    data = tmp_path / "data"
    data.mkdir()
    (data / "inbox.json").write_text(json.dumps([dict(ITEMS[1], id="mine")]))
    assert legacy_import.import_walkietalkie(data, source) == 0
    assert [i.id for i in Inbox(data).items] == ["mine"]


def test_no_walkietalkie_is_fine(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    assert legacy_import.import_walkietalkie(data, tmp_path / "missing") == 0
    assert (data / "imported.json").exists()


def test_a_clip_name_that_climbs_out_is_not_followed(tmp_path):
    source = walkie(tmp_path, [dict(ITEMS[0], voice_file="../../secret")])
    data = tmp_path / "data"
    data.mkdir()
    legacy_import.import_walkietalkie(data, source)
    assert Inbox(data).items[0].voice_file == ""
