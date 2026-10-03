"""Received messages, kept on disk so they survive a restart.

Voice payloads are stored as the **codec2 bitstream**, not decoded PCM:
a 20-second clip is 2 kB encoded against 320 kB as PCM, which matters on
a Pi that may be running from a small SD card, and decoding on replay
costs a few milliseconds.

The inbox is capped at `limit` entries and trimmed on write, so an
unattended radio left receiving overnight cannot fill the card.

Voice this radio sends is kept too, with the message number it went out
under: a radio that received it with gaps can ask for the missing parts
again (Receive, three clicks), and one that missed it can ask for all of
it once back in range. A received message keeps which fragments never
arrived, so what comes later can be put in its place.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field, fields

from app.radio import protocol
from app.utils.logger import get_logger

log = get_logger("inbox")

INDEX_FILE = "inbox.json"
VOICE_DIR = "voice"
DEFAULT_LIMIT = 200      # text and voice; a voice clip is about 2 kB on disk

# A sent text, from the moment it is queued until the other radio answers.
SENDING, SENT, DELIVERED, FAILED = "sending", "sent", "delivered", "failed"


@dataclass
class Item:
    id: str
    kind: str            # "text" | "voice"
    src: int
    peer_name: str
    received_at: float
    rssi_dbm: int | None = None
    text: str = ""
    voice_file: str = ""
    codec_mode: int = 0
    duration: float = 0.0
    incomplete: bool = False
    played: bool = False
    outgoing: bool = False
    # How the message travelled, so it can be asked for again. -1: before
    # this was kept, and it cannot be.
    msg_id: int = -1
    total: int = 0
    fragment_size: int = 0
    missing: list = field(default_factory=list)
    dst: int = protocol.BROADCAST
    retrieving: bool = False
    retrieve_tries: int = 0
    # What we sent (text or voice): SENDING, SENT (on the air), DELIVERED
    # (the other radio acknowledged it), FAILED (never went out).
    status: str = ""

    @property
    def can_retrieve(self) -> bool:
        return (not self.outgoing and self.kind == "voice" and self.msg_id >= 0
                and bool(self.missing) and self.total > 0)

    @property
    def when(self) -> str:
        age = time.time() - self.received_at
        if age < 60:
            return "just now"
        if age < 3600:
            return f"{int(age // 60)}m"
        if age < 86400:
            return f"{int(age // 3600)}h"
        return time.strftime("%d %b", time.localtime(self.received_at))

    @property
    def summary(self) -> str:
        if self.kind == "text":
            return self.text[:40]
        if self.retrieving:
            mark = " (fetching…)"
        elif self.incomplete and self.can_retrieve:
            mark = " (gaps · play to fix)"
        else:
            mark = " (gaps)" if self.incomplete else ""
        return f"voice {self.duration:.1f}s{mark}"


class Inbox:
    def __init__(self, data_dir, limit: int = DEFAULT_LIMIT):
        self.dir = data_dir
        self.voice_dir = data_dir / VOICE_DIR
        self.voice_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / INDEX_FILE
        self.limit = limit
        self.items = self._load()

    def _load(self) -> list:
        if not self.path.is_file():
            return []
        known = {f.name for f in fields(Item)}
        try:
            items = [Item(**{k: v for k, v in entry.items() if k in known})
                     for entry in json.loads(self.path.read_text())]
        except Exception:
            log.warning("could not read %s; starting empty", self.path)
            return []
        for item in items:
            item.retrieving = False     # whatever was asked for went unanswered
            if item.status == SENDING:
                item.status = FAILED    # queued when the app stopped: never sent
        return items

    def save(self):
        # Written beside the old file, then swapped in: a power cut mid-write
        # must not lose every message.
        temp = self.path.with_name(self.path.name + ".tmp")
        try:
            temp.write_text(json.dumps([asdict(i) for i in self.items]))
            os.replace(temp, self.path)
        except OSError:
            log.warning("could not write inbox index", exc_info=True)

    @property
    def unread(self) -> int:
        return sum(1 for item in self.items if not item.played and not item.outgoing)

    def add_text(self, message, peer_name: str, outgoing: bool = False) -> Item:
        item = Item(
            id=uuid.uuid4().hex[:12], kind="text", src=message.src,
            peer_name=peer_name, received_at=message.received_at,
            rssi_dbm=message.rssi_dbm,
            text=message.body.decode("utf-8", "replace"),
            incomplete=not message.complete, outgoing=outgoing,
            msg_id=message.msg_id, dst=message.dst,
        )
        return self._append(item)

    def add_sent_text(self, src: int, dst: int, peer_name: str, text: str) -> Item:
        """A text we are sending; ``sent`` sets its message number once queued."""
        item = Item(id=uuid.uuid4().hex[:12], kind="text", src=src, peer_name=peer_name,
                    received_at=time.time(), text=text, outgoing=True, played=True,
                    dst=dst, status=SENDING)
        return self._append(item)

    def set_status(self, item: Item, status: str, msg_id: int | None = None):
        item.status = status
        if msg_id is not None:
            item.msg_id = msg_id
        self.save()

    def sending(self, msg_id: int):
        """The newest message we sent under ``msg_id`` still going out, or None."""
        return next((i for i in self.items if i.outgoing and i.msg_id == msg_id
                     and i.status == SENDING), None)

    def delivered(self, src: int, msg_id: int):
        """``src`` acknowledged our message ``msg_id`` (text or voice).
        Returns the item, if any.

        Message numbers wrap at 256, so only one still waiting counts.
        """
        item = next((i for i in self.items if i.outgoing and i.dst == src
                     and i.msg_id == msg_id and i.status in (SENDING, SENT)), None)
        if item is not None:
            self.set_status(item, DELIVERED)
        return item

    def thread(self, address: int) -> list:
        """One conversation, oldest first: texts and voice to and from ``address``.

        ``protocol.BROADCAST`` is the "Everyone" thread: what went to all.
        """
        if address == protocol.BROADCAST:
            items = [i for i in self.items if i.dst == protocol.BROADCAST]
        else:
            items = [i for i in self.items
                     if (i.outgoing and i.dst == address)
                     or (not i.outgoing and i.src == address and i.dst != protocol.BROADCAST)]
        return list(reversed(items))

    def mark_thread_read(self, address: int) -> int:
        """Mark a conversation's texts read (voice stays new until played)."""
        changed = 0
        for item in self.thread(address):
            if item.kind == "text" and not item.played and not item.outgoing:
                item.played = True
                changed += 1
        if changed:
            self.save()
        return changed

    def add_voice(self, message, peer_name: str, duration: float,
                  outgoing: bool = False, store_audio: bool = True,
                  status: str = "") -> Item:
        """Keep a voice message, received or sent.

        What we send is kept as well as what we receive: it is the copy
        another radio's request for the message is answered from.
        """
        item_id = uuid.uuid4().hex[:12]
        filename = ""
        if store_audio and message.body:
            filename = f"{item_id}.c2"
            try:
                (self.voice_dir / filename).write_bytes(message.body)
            except OSError:
                log.warning("could not store voice payload", exc_info=True)
                filename = ""
        item = Item(
            id=item_id, kind="voice", src=message.src, peer_name=peer_name,
            received_at=message.received_at, rssi_dbm=message.rssi_dbm,
            voice_file=filename, codec_mode=message.flags, duration=duration,
            incomplete=not message.complete, outgoing=outgoing,
            msg_id=message.msg_id, total=message.total,
            fragment_size=message.fragment_size or protocol.VOICE_CHUNK,
            missing=list(message.missing), dst=message.dst, status=status,
            played=outgoing,
        )
        return self._append(item)

    # --- asking for a message again ------------------------------------------
    def find(self, src, msg_id: int, total: int, outgoing: bool = False):
        """The newest kept message this describes, or None. `src` None
        matches any sender -- for our own, sent under an earlier Device ID."""
        return next((i for i in self.items
                     if i.kind == "voice" and i.outgoing == outgoing
                     and (src is None or i.src == src)
                     and i.msg_id == msg_id and i.total == total), None)

    def sent_since(self, since: float, limit: int) -> list:
        """Our own voice messages sent after `since` (wall clock), newest first."""
        return [i for i in self.items
                if i.outgoing and i.kind == "voice" and i.msg_id >= 0
                and i.voice_file and i.received_at >= since][:limit]

    def held_check(self, item: Item):
        """(seq, fingerprint) of a fragment this item holds, to name it by."""
        data = self.voice_bytes(item)
        size = item.fragment_size or protocol.VOICE_CHUNK
        held = [s for s in range(item.total) if s not in item.missing]
        if not data or not held:
            return protocol.NO_CHECK, 0
        return held[0], protocol.chunk_check(data, held[0], size)

    def merge(self, item: Item, message) -> list:
        """Put fragments that came again into `item`. Returns their numbers.

        Only fragments the item was missing are taken, into the places
        kept for them. What is still missing stays as it was stored:
        silence, of the right length.
        """
        size = item.fragment_size or message.fragment_size or protocol.VOICE_CHUNK
        arrived = [s for s in item.missing
                   if s not in message.missing and s < message.total]
        if not arrived or not item.voice_file:
            return []
        body = bytearray(self.voice_bytes(item))
        for seq in arrived:
            chunk = protocol.chunk_of(message.body, seq, size)
            start = seq * size
            if len(body) < start:
                body.extend(bytes(start - len(body)))
            if seq == item.total - 1:
                body[start:] = chunk          # the last may be short
            else:
                body[start:start + size] = chunk
        item.missing = [s for s in item.missing if s not in arrived]
        item.incomplete = bool(item.missing)
        try:
            (self.voice_dir / item.voice_file).write_bytes(bytes(body))
        except OSError:
            log.warning("could not store the fragments that came again", exc_info=True)
            return []
        self.save()
        return arrived

    def voice_bytes(self, item: Item) -> bytes:
        if not item.voice_file:
            return b""
        try:
            return (self.voice_dir / item.voice_file).read_bytes()
        except OSError:
            return b""

    def _append(self, item: Item) -> Item:
        self.items.insert(0, item)
        for stale in self.items[self.limit:]:
            if stale.voice_file:
                (self.voice_dir / stale.voice_file).unlink(missing_ok=True)
        del self.items[self.limit:]
        self.save()
        return item

    def mark_played(self, item: Item):
        item.played = True
        self.save()

    def latest_voice(self):
        return next(
            (i for i in self.items if i.kind == "voice" and not i.outgoing), None
        )
