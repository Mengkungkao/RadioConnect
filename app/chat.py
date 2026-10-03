"""Chats: one conversation per radio, texts and voice together.

What Messenger brought to RadioConnect, on WalkieTalkie's radio protocol:

* **Chats** lists "Everyone" and every paired radio, newest first, with a
  preview and how many are unread.
* **A conversation** shows texts and voice as bubbles, oldest at the top.
  It is a talk screen: hold (or Space) sends a voice message to that radio,
  typing on a keyboard writes a text (Enter sends, Esc cancels), and the
  Reply row (3× or Enter) offers ready-made answers for the button alone.
* **Delivery ticks.** A text to one radio is acknowledged (protocol ACK,
  which WalkieTalkie defines but never sends). ✓ means on the air, ✓✓
  delivered. WalkieTalkie radios ignore ACKs, so texts to them stop at ✓.
  Texts to everyone are not acknowledged: a dozen answers would collide.

The controller only calls the app's public pieces (inbox, link, state);
the screens draw from ``ViewState`` and never call back into here.
"""

from __future__ import annotations

from app.radio import protocol
from app.radio.link import NotPaired
from app.store import inbox as store
from app.store.roster import BROADCAST_NAME
from app.ui.screens import CHAT, CHATS, REPLIES
from app.utils.logger import get_logger

log = get_logger("chat")

EVERYONE = "Everyone"
QUICK_REPLIES = ("OK", "On my way", "Yes", "No", "Call me", "Where are you?",
                 "I'm safe", "Need help")
MAX_TEXT = 180            # one fragment: 190 bytes less the seal, with room
REPLY_ROW, BACK_ROW = "reply", "back"


class ChatController:
    def __init__(self, app):
        self.app = app
        self.compose: str | None = None       # the text being typed, if any
        self._early = set()   # message numbers sent before they were recorded

    # --- what the screens show --------------------------------------------
    @property
    def state(self):
        return self.app.state

    @property
    def inbox(self):
        return self.app.inbox

    @property
    def composing(self) -> bool:
        return self.compose is not None

    def conversations(self) -> list:
        """Rows for Chats: Everyone, then each paired radio, newest first."""
        rows = []
        for address, name in [(protocol.BROADCAST, EVERYONE)] + self._paired():
            thread = self.inbox.thread(address)
            last = thread[-1] if thread else None
            unread = sum(1 for i in thread if not i.outgoing and not i.played)
            preview = (("You: " if last.outgoing else "") + _summary(last)) if last \
                else ("every paired radio" if address == protocol.BROADCAST else "no messages yet")
            rows.append({"key": address, "label": name, "unread": unread,
                         "value": (f"{unread} new · " if unread else "") + preview,
                         "at": last.received_at if last else 0.0})
        everyone, others = rows[0], rows[1:]
        others.sort(key=lambda row: -row["at"])
        return [everyone] + others + [{"key": BACK_ROW, "label": "Back"}]

    def refresh(self):
        state = self.state
        state.chats = self.conversations()
        state.chats_index = min(state.chats_index, len(state.chats) - 1)
        if state.chat_peer is not None:
            state.chat_items = self.inbox.thread(state.chat_peer[0])
            rows = len(state.chat_items) + 2           # bubbles, Reply, Back
            state.chat_index = min(state.chat_index, rows - 1)
        state.compose = self.compose
        state.unread = self.inbox.unread

    # --- Chats ---------------------------------------------------------------
    def open_list(self):
        self.state.chats_index = 0
        self.refresh()
        self.app._show(CHATS)

    def next_conversation(self, step: int = 1):
        count = len(self.state.chats)
        self.state.chats_index = (self.state.chats_index + step) % count

    def open_conversation(self):
        row = self.state.chats[self.state.chats_index]
        if row["key"] == BACK_ROW:
            self.app._go_back()
            return
        self.open_thread(row["key"], row["label"])

    # --- one conversation ------------------------------------------------------
    def open_thread(self, address: int, name: str):
        self.state.chat_peer = (address, name)
        self.compose = None
        # Holding the button here talks to this radio.
        self.app._target = (address, BROADCAST_NAME if address == protocol.BROADCAST else name)
        self.app._refresh_entries()
        self.inbox.mark_thread_read(address)
        self.refresh()
        self.state.chat_index = len(self.state.chat_items)   # the Reply row
        self.app._show(CHAT)

    def selected(self):
        """The selected bubble's item, REPLY_ROW or BACK_ROW."""
        items = self.state.chat_items
        index = self.state.chat_index
        if index < len(items):
            return items[index]
        return REPLY_ROW if index == len(items) else BACK_ROW

    def next_bubble(self, step: int = 1):
        rows = len(self.state.chat_items) + 2
        self.state.chat_index = (self.state.chat_index + step) % rows

    def open_bubble(self):
        chosen = self.selected()
        if chosen == BACK_ROW:
            self.leave_thread()
        elif chosen == REPLY_ROW or chosen.kind == "text":
            self.state.replies_index = 0
            self.app._show(REPLIES)
        else:
            self.app._play_item(chosen, fetch_gaps=True)

    def leave_thread(self):
        self.compose = None
        self.state.compose = None
        self.app._go_back()

    # --- quick replies -------------------------------------------------------------
    def reply_items(self) -> list:
        return ([{"key": text, "label": text} for text in QUICK_REPLIES]
                + [{"key": BACK_ROW, "label": "Back"}])

    def next_reply(self, step: int = 1):
        count = len(QUICK_REPLIES) + 1
        self.state.replies_index = (self.state.replies_index + step) % count

    def send_reply(self):
        index = self.state.replies_index
        self.app._go_back()                       # to the conversation
        if index < len(QUICK_REPLIES):
            self.send(QUICK_REPLIES[index])

    # --- typing ---------------------------------------------------------------------
    def type_char(self, char: str):
        if not char.isprintable():
            return
        draft = self.compose or ""
        if len((draft + char).encode("utf-8")) <= MAX_TEXT:
            self.compose = draft + char
        self.state.compose = self.compose

    def erase(self):
        if self.compose:
            self.compose = self.compose[:-1]
        self.state.compose = self.compose

    def cancel_compose(self):
        self.compose = None
        self.state.compose = None

    def submit(self):
        text = (self.compose or "").strip()
        self.cancel_compose()
        if text:
            self.send(text)

    # --- sending and receiving ---------------------------------------------------------
    def send(self, text: str) -> bool:
        address, name = self.state.chat_peer or (protocol.BROADCAST, EVERYONE)
        link = self.app.link
        if link is None:
            self.state.flash("radio offline", 3.0)
            return False
        if not link.can_send(address):
            self.state.flash(f"pair with {name} first", 3.0)
            return False
        item = self.inbox.add_sent_text(self.app.settings.radio.address, address, name, text)
        try:
            msg_id = link.send_text(address, text)
        except NotPaired:
            self.inbox.set_status(item, store.FAILED)
            self.state.flash(f"pair with {name} first", 3.0)
            return False
        self.inbox.set_status(item, store.SENDING, msg_id)
        self.track_outgoing(item)
        log.info("text %d to %s queued (%d bytes)", msg_id, name, len(text.encode("utf-8")))
        self.state.chat_index = len(self.inbox.thread(address))   # keep Reply selected
        self.refresh()
        return True

    def acknowledge(self, message):
        """Tell the sender a text or voice message for us arrived. Broadcasts
        are not acknowledged: every radio answering at once would collide."""
        if message.dst != self.app.settings.radio.address or self.app.link is None:
            return
        try:
            self.app.link.send_ack(message.src, message.msg_id)
        except NotPaired:
            log.debug("no keys to acknowledge %d", message.src)

    def on_voice(self, message, item):
        """A voice message arrived (already in the inbox). On the receive thread."""
        self.acknowledge(message)
        self.refresh()

    def on_text(self, message, item):
        """A text arrived (already in the inbox). On the receive thread."""
        if message.complete:
            self.acknowledge(message)
        peer = self.state.chat_peer
        thread = protocol.BROADCAST if message.dst == protocol.BROADCAST else message.src
        if peer is not None and peer[0] == thread and self.state.screen == CHAT:
            self.inbox.mark_played(item)            # it is on screen
            if self.state.chat_index >= len(self.state.chat_items):
                self.state.chat_index += 1          # keep Reply/Back selected
        self.refresh()

    def on_ack(self, message):
        """The other radio confirmed a message arrived: say so, then tick it."""
        if not message.body:
            return
        item = self.inbox.delivered(message.src, message.body[0])
        if item is None:
            return
        what = "message" if item.kind == "text" else f"{item.duration:.0f}s voice"
        self.state.flash(f"✓✓ {item.peer_name or 'they'} got your {what}", 4.0)
        self.refresh()

    def on_sent(self, label: str, ok: bool):
        kind, _, number = label.partition("/")
        if kind not in ("text", "voice") or not number.isdigit():
            return
        item = self.inbox.sending(int(number))
        if item is None:
            if ok:
                self._early.add(int(number))
            return
        self._mark_sent(item, ok)

    def track_outgoing(self, item):
        """A message just recorded as SENDING; its "sent" may already be in."""
        if item.msg_id in self._early:
            self._early.discard(item.msg_id)
            self._mark_sent(item, True)

    def _mark_sent(self, item, ok: bool):
        # To everyone it stays at SENT: nobody acknowledges a broadcast.
        self.inbox.set_status(item, store.SENT if ok else store.FAILED)
        if not ok:
            self.state.flash(f"not sent to {item.peer_name or 'them'}", 4.0)
        elif item.dst == protocol.BROADCAST:
            self.state.flash("✓ sent to everyone", 3.0)
        else:
            self.state.flash(f"✓ sent · waiting for {item.peer_name or 'them'}", 4.0)
        self.refresh()

    # --- helpers -------------------------------------------------------------------------
    def _paired(self) -> list:
        return [(entry.address, entry.name) for entry in self.app.roster.entries()
                if not entry.is_broadcast and entry.address not in self.state.unpaired]


def _summary(item) -> str:
    if item.kind == "text":
        return item.text
    return f"voice {item.duration:.0f}s"
