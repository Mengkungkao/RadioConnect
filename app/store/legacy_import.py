"""Bring WalkieTalkie's received and sent messages into RadioConnect, once.

RadioConnect is meant to replace WalkieTalkie and Messenger. The radio
identity, keys and contacts already come through mFruit OS's shared radio
store; this copies WalkieTalkie's message list (``inbox.json`` and its voice
clips) so removing WalkieTalkie later loses no history. It uses the same
format and the same Device IDs, so the messages land in the right chats.

Copied, never moved: WalkieTalkie's files are left exactly as they were. It
runs once (``imported.json`` records it), and only into an empty inbox, so
nothing of RadioConnect's own is ever overwritten.

Messenger's history is not imported: its old Device IDs were derived from the
hostname and match no contact, so its texts would have nowhere to go.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from app.store.inbox import FAILED, INDEX_FILE, SENDING, VOICE_DIR
from app.utils.logger import get_logger

log = get_logger("import")

MARKER = "imported.json"


def walkie_data_dir() -> Path:
    return Path(os.environ.get("WALKIE_DATA_DIR") or Path.home() / ".whisplay-walkie")


def _has_messages(path: Path) -> bool:
    try:
        return bool(json.loads(path.read_text() or "[]"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True          # unreadable: leave it alone rather than replace it


def import_walkietalkie(data_dir: Path, source: Path | None = None) -> int:
    """Copy WalkieTalkie's messages into ``data_dir``. Returns how many."""
    source = source or walkie_data_dir()
    marker = data_dir / MARKER
    if marker.exists() or source.resolve() == data_dir.resolve():
        return 0
    count = 0
    target = data_dir / INDEX_FILE
    try:
        items = json.loads((source / INDEX_FILE).read_text())
    except FileNotFoundError:
        items = []
    except (OSError, ValueError) as exc:
        log.warning("cannot read WalkieTalkie's messages (%s); not importing them", exc)
        items = []
    if items and not _has_messages(target):
        (data_dir / VOICE_DIR).mkdir(parents=True, exist_ok=True)
        for item in items:
            clip = str(item.get("voice_file") or "")
            if clip and (os.path.basename(clip) != clip or clip.startswith(".")):
                item["voice_file"] = clip = ""             # not a plain file name
            if clip:
                try:
                    shutil.copy2(source / VOICE_DIR / clip, data_dir / VOICE_DIR / clip)
                except OSError:
                    item["voice_file"] = ""          # the clip itself is gone
            if item.get("status") == SENDING:
                item["status"] = FAILED
        temp = target.with_name(target.name + ".tmp")
        temp.write_text(json.dumps(items))
        os.replace(temp, target)
        count = len(items)
        log.info("copied %d message(s) from WalkieTalkie (%s); its files are kept",
                 count, source)
    marker.write_text(json.dumps({"walkietalkie": str(source), "messages": count,
                                  "at": time.strftime("%Y-%m-%d %H:%M:%S")}))
    return count
