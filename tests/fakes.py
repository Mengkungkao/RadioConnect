"""Fake SX126X modules wired to each other over a fake channel.

These model the module, not just a serial port, because the module is
not transparent in the way a naive fake would suggest. In fixed-point
transmission mode it **consumes the first three bytes of every write**
(destination address high, low, and channel) and puts only the rest on
the air; with RSSI reporting enabled it **appends one byte** to every
packet it delivers. Both behaviours are what the framing layer has to
cope with, so both belong in the fake -- a fake that just forwarded
bytes would let a broken deframer pass.
"""

from __future__ import annotations

import threading
from collections import deque

# Address the module treats as "everyone".
BROADCAST = 0xFFFF

# What the fake reports as signal strength: byte b means -(256 - b) dBm.
DEFAULT_RSSI_BYTE = 0xA5  # -91 dBm


class FakeModule:
    """One SX126X, as seen through its UART."""

    def __init__(self, name: str = "module", addr: int = 0,
                 rssi_byte: int | None = DEFAULT_RSSI_BYTE):
        self.name = name
        self.addr = addr
        self.rssi_byte = rssi_byte
        self.peers = []
        self._buffer = deque()
        self._cond = threading.Condition()
        self._closed = False
        self._cancelled = False
        self.written = bytearray()      # everything the host handed us
        self.transmitted = []           # what actually went on the air

    @staticmethod
    def pair(rssi_byte: int | None = DEFAULT_RSSI_BYTE):
        left = FakeModule("left", addr=1, rssi_byte=rssi_byte)
        right = FakeModule("right", addr=2, rssi_byte=rssi_byte)
        left.peers.append(right)
        right.peers.append(left)
        return left, right

    @staticmethod
    def network(count: int, rssi_byte: int | None = DEFAULT_RSSI_BYTE):
        """`count` modules that all hear each other, addressed 1..count."""
        modules = [FakeModule(f"node{i}", addr=i, rssi_byte=rssi_byte)
                   for i in range(1, count + 1)]
        for module in modules:
            module.peers.extend(m for m in modules if m is not module)
        return modules

    # --- pyserial surface ---------------------------------------------
    @property
    def in_waiting(self) -> int:
        with self._cond:
            return len(self._buffer)

    def write(self, data: bytes) -> int:
        self.written.extend(data)
        if len(data) < 3:
            return len(data)
        dst = (data[0] << 8) | data[1]
        payload = data[3:]
        self.transmitted.append(payload)
        self._air(dst, payload)
        return len(data)

    def _air(self, dst: int, payload: bytes):
        for peer in self.peers:
            if dst in (BROADCAST, peer.addr) or peer is self:
                peer._receive(payload)

    def _receive(self, payload: bytes):
        with self._cond:
            if self._closed:
                return
            self._buffer.extend(payload)
            if self.rssi_byte is not None:
                self._buffer.append(self.rssi_byte)
            self._cond.notify_all()

    def flush(self):
        pass

    def read(self, size: int = 1) -> bytes:
        out = bytearray()
        with self._cond:
            while len(out) < size:
                while not self._buffer and not self._closed and not self._cancelled:
                    self._cond.wait(timeout=2.0)
                    if not self._buffer:
                        break
                if self._closed or self._cancelled or not self._buffer:
                    break
                out.append(self._buffer.popleft())
        return bytes(out)

    def reset_input_buffer(self):
        with self._cond:
            self._buffer.clear()

    def cancel_read(self):
        with self._cond:
            self._cancelled = True
            self._cond.notify_all()

    def close(self):
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    # --- test helpers ---------------------------------------------------
    def inject(self, data: bytes):
        """Deliver bytes as if they arrived off the air."""
        self._receive(data)


class LossyModule(FakeModule):
    """Drops whole packets, the way a marginal RF link does."""

    def __init__(self, name: str = "lossy", addr: int = 1, drop_indices=()):
        super().__init__(name, addr=addr)
        self.drop_indices = set(drop_indices)
        self.packet_index = 0

    def _air(self, dst: int, payload: bytes):
        index = self.packet_index
        self.packet_index += 1
        if index in self.drop_indices:
            return  # transmitted into the void
        super()._air(dst, payload)


class Air:
    """A shared channel where packets take time and can collide.

    A packet is on the air from when its module starts it (once the
    module's previous packet has finished) for ``seconds_per_byte`` per
    byte plus ``overhead``. A receiver gets it only if, all that time, the
    receiver was not transmitting itself (half duplex) and no third packet
    overlapped it (a collision). Real time, so the links' own threads and
    pacing run as they do on a device.
    """

    def __init__(self, seconds_per_byte: float = 8 / 9600 * 0.8, overhead: float = 0.05):
        import time as _time
        self.clock = _time.monotonic
        self.seconds_per_byte = seconds_per_byte
        self.overhead = overhead
        self.lock = threading.Lock()
        self.flights = []          # (start, end, sender)
        self.lost = []             # (receiver name, sender name, payload size)

    def on_air(self, module, now=None) -> bool:
        """Is anyone other than ``module`` transmitting right now?"""
        now = self.clock() if now is None else now
        with self.lock:
            return any(s <= now < e and m is not module for s, e, m in self.flights)

    def transmitting(self, module, now=None) -> bool:
        now = self.clock() if now is None else now
        with self.lock:
            return any(s <= now < e and m is module for s, e, m in self.flights)

    def send(self, sender, dst: int, payload: bytes):
        now = self.clock()
        with self.lock:
            busy_until = max([e for _, e, m in self.flights if m is sender] + [now])
            start = busy_until
            end = start + self.overhead + len(payload) * self.seconds_per_byte
            self.flights.append((start, end, sender))
        timer = threading.Timer(end - now, self._land, (sender, dst, payload, start, end))
        timer.daemon = True
        timer.start()

    def _land(self, sender, dst: int, payload: bytes, start: float, end: float):
        with self.lock:
            others = [(s, e, m) for s, e, m in self.flights
                      if (s, e, m) != (start, end, sender) and s < end and e > start]
            cutoff = self.clock() - 30
            self.flights = [f for f in self.flights if f[1] > cutoff]
        for peer in sender.peers:
            if peer is sender or dst not in (BROADCAST, peer.addr):
                continue
            if any(m is peer or m is not sender for _, _, m in others):
                self.lost.append((peer.name, sender.name, len(payload)))
                continue
            peer._receive(payload)


class AirModule(FakeModule):
    """A FakeModule on an ``Air`` channel. With ``reports_noise`` it answers
    the channel-level query (C0 C1 C2 C3 00 01) as the E22 does: C1 00 01
    and the level, ``busy_dbm`` while another module is on the air."""

    NOISE_QUERY = bytes([0xC0, 0xC1, 0xC2, 0xC3, 0x00, 0x01])

    def __init__(self, air: Air, name: str, addr: int, reports_noise: bool = False,
                 floor_dbm: int = -110, busy_dbm: int = -50):
        super().__init__(name, addr=addr)
        self.air = air
        self.reports_noise = reports_noise
        self.floor_dbm = floor_dbm
        self.busy_dbm = busy_dbm
        self.noise_queries = 0

    @staticmethod
    def network(air: Air, count: int, **kwargs):
        modules = [AirModule(air, f"node{i}", addr=i, **kwargs) for i in range(1, count + 1)]
        for module in modules:
            module.peers.extend(m for m in modules if m is not module)
        return modules

    def write(self, data: bytes) -> int:
        if bytes(data) == self.NOISE_QUERY:
            self.noise_queries += 1
            if self.reports_noise:
                level = self.busy_dbm if self.air.on_air(self) else self.floor_dbm
                with self._cond:
                    self._buffer.extend(bytes([0xC1, 0x00, 0x01, 256 + level]))
                    self._cond.notify_all()
            return len(data)
        return super().write(data)

    def _air(self, dst: int, payload: bytes):
        self.air.send(self, dst, payload)
