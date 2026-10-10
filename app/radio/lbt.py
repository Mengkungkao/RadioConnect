"""Listen before talk: when this radio may start a message.

The radio is half duplex. While it transmits it hears nothing, and two
radios that transmit at once usually both lose. The log that prompted
this showed it: one radio answered a ping while the other was already
sending a voice message, and the first two fragments of that message were
lost.

So before the first packet of each message the link asks this object
whether the channel is clear. It knows four things:

* **Messages in progress.** Every packet's header is readable, even one
  sealed for somebody else or on another privacy channel, and it says
  "fragment ``seq`` of ``total``". A radio heard sending fragment 2 of 6
  will be on the air for four more fragments, so the channel is busy until
  then (re-armed with each fragment, and never more than
  ``AHEAD_FRAGMENTS`` ahead, so a lost last fragment does not hold us up).
* **Room to answer.** After a request (a text, voice, ping, hello, ...) to
  one radio, that radio answers at once (an ACK, a pong, ...). Everyone
  else, the sender included, leaves it ``reply_seconds`` to do so. The
  answer itself is exempt, and so is anything we send in answer.
* **Bytes arriving.** A packet is being handed over by the module right now.
* **Our own last packet,** still on the air after the write returned.

The link adds a fifth when the module can measure it: the signal level on
the channel (``NoiseFloor``), which also catches the first fragment of a
message, before any of it has been heard.

After waiting for a busy channel, a message that is not an answer waits a
further random ``0..JITTER_SECONDS``, so that radios which were all waiting
do not all start at the same instant. Waits are bounded by the link.
"""

from __future__ import annotations

import threading
import time

from app.radio import protocol

# The channel counts as busy this long after the last byte from the module.
BYTE_GUARD = 0.1
# And this long after a packet that ends its message (the sender may have
# another message queued; this gives its receivers the first turn).
AFTER_MESSAGE_GUARD = 0.15
# A message in progress is expected to last at most this many more
# fragments beyond the one just heard.
AHEAD_FRAGMENTS = 3
# The answering radio needs this long, on top of its answer's airtime, to
# hear the request over the UART, handle it and check the channel itself.
REPLY_MARGIN = 0.7
# A short answer (ACK, pong): header, sealing and framing.
REPLY_FRAME_BYTES = 48
# The random extra wait after a busy channel, for messages that are not
# answers.
JITTER_SECONDS = 0.6

# Requests to one radio that it answers straight away, and the answers.
REQUEST_TYPES = frozenset({protocol.HELLO, protocol.TEXT, protocol.VOICE,
                           protocol.PAIR_REQUEST, protocol.REPAIR, protocol.PING,
                           protocol.RETRIEVE})
REPLY_TYPES = frozenset({protocol.ACK, protocol.PONG, protocol.HELLO_ACK,
                         protocol.REJECT, protocol.PAIR_ACCEPT, protocol.RESENT})


def expects_answer(type_: int, dst: int) -> bool:
    return dst != protocol.BROADCAST and type_ in REQUEST_TYPES


class NoiseFloor:
    """The channel's signal level, as the module reports it.

    Busy when a reading is ``margin_db`` above the quietest of the recent
    readings, or at or above ``strong_dbm`` whatever the floor. LoRa is
    received below the noise, so this catches nearby radios, not distant
    ones; distant ones are caught when their fragments are heard.
    """

    def __init__(self, margin_db: float = 10.0, strong_dbm: float = -80.0,
                 memory: int = 16):
        self.margin_db = margin_db
        self.strong_dbm = strong_dbm
        self.memory = memory
        self._readings: list = []

    @property
    def floor(self):
        return min(self._readings) if self._readings else None

    def busy(self, dbm: float) -> bool:
        """Record one reading; True if the channel is in use."""
        floor = self.floor
        self._readings.append(dbm)
        del self._readings[:-self.memory]
        if dbm >= self.strong_dbm:
            return True
        return floor is not None and dbm >= floor + self.margin_db


class ChannelSense:
    """What this radio knows about the channel, from what it has heard.

    Fed by the receive thread (``bytes_arrived``, ``heard``) and the
    transmit thread (``transmitted``, ``expect_answer``); read by the
    transmit thread (``busy``). Times are ``time.monotonic`` seconds.
    """

    def __init__(self, fragment_seconds: float, reply_seconds: float,
                 clock=time.monotonic):
        self.fragment_seconds = fragment_seconds
        self.reply_seconds = reply_seconds
        self.clock = clock
        self._lock = threading.Lock()
        self._talkers: dict = {}             # src -> (until, why)
        self._activity_until = 0.0
        self._own_until = 0.0
        self._answer_from: int | None = None
        self._answer_until = 0.0

    # --- what was heard ---------------------------------------------------
    def bytes_arrived(self, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        with self._lock:
            self._activity_until = max(self._activity_until, now + BYTE_GUARD)

    def heard(self, src: int, dst: int, type_: int, seq: int, total: int,
              me: int, now: float | None = None) -> None:
        """A packet from another radio, whoever it was for."""
        now = self.clock() if now is None else now
        remaining = max(0, total - 1 - seq)
        if remaining:
            ahead = min(remaining, AHEAD_FRAGMENTS)
            entry = (now + ahead * self.fragment_seconds + AFTER_MESSAGE_GUARD,
                     f"{src} is sending ({seq + 1} of {total})")
        elif expects_answer(type_, dst) and dst != me:
            entry = (now + self.reply_seconds, f"room for {dst} to answer {src}")
        else:
            entry = (now + AFTER_MESSAGE_GUARD, f"{src} just sent")
        with self._lock:
            self._talkers[src] = entry
            if src == self._answer_from:
                self._answer_from, self._answer_until = None, 0.0

    # --- what we sent -----------------------------------------------------
    def transmitted(self, airtime: float, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        with self._lock:
            self._own_until = max(self._own_until, now + airtime)

    def expect_answer(self, dst: int, now: float | None = None) -> None:
        """We finished a request to ``dst``: leave it room to answer."""
        now = self.clock() if now is None else now
        with self._lock:
            self._answer_from = dst
            self._answer_until = max(now, self._own_until) + self.reply_seconds

    # --- the question -------------------------------------------------------
    def busy(self, now: float | None = None, answering: bool = False) -> tuple:
        """(until, why): the channel is expected clear at ``until``; clear
        now when ``until <= now``. ``answering``: the message is an answer,
        which does not wait for answers to our own requests."""
        now = self.clock() if now is None else now
        found = []
        with self._lock:
            for src in [s for s, (until, _) in self._talkers.items() if until <= now]:
                del self._talkers[src]
            found += self._talkers.values()
            if self._activity_until > now:
                found.append((self._activity_until, "receiving"))
            if self._own_until > now:
                found.append((self._own_until, "our last packet is on the air"))
            if not answering and self._answer_until > now:
                found.append((self._answer_until,
                              f"waiting for {self._answer_from} to answer"))
        return max(found) if found else (0.0, "")
