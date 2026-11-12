"""The transport interface. Server, client and bots talk to this and nothing else,
which is what lets one game implementation run over both TCP and UDP.

A `peer` is an opaque integer naming one remote endpoint. Messages are the dicts
defined in common/protocol.py. A transport is single-threaded: all socket work
happens inside recv(), which the owner must call often (every few tens of
milliseconds) because timers such as retransmits and timeouts run there too.
"""

from abc import ABC, abstractmethod
from collections import Counter

from common import protocol


class Transport(ABC):
    def __init__(self):
        self._disconnect_callback = None
        self._lost = []      # (peer, reason) whose disconnect callback is still due
        self._totals = Counter()
        self._per_peer = {}  # peer -> Counter

    def send(self, peer, message):
        """Send with the reliability class the protocol assigns to this message type."""
        if protocol.is_reliable(message["type"]):
            self.send_reliable(peer, message)
        else:
            self.send_unreliable(peer, message)

    @abstractmethod
    def send_reliable(self, peer, message):
        """Must arrive, in order."""

    @abstractmethod
    def send_unreliable(self, peer, message):
        """Fire and forget; may be lost, the newest one wins."""

    @abstractmethod
    def recv(self, timeout=0.0):
        """Do pending socket work for up to `timeout` seconds.
        Returns a list of (peer, message). Disconnect callbacks also fire from here."""

    @abstractmethod
    def authenticate(self, peer, token):
        """Bind the session token from LOGIN_OK to this peer. Both sides call it
        once login has succeeded. What it means is up to the transport."""

    def rtt_sample(self, peer, rtt_ms):
        """A round-trip time measured with PING/PONG. A transport that does its
        own retransmission uses it to set the timer; TCP's kernel has its own."""

    @abstractmethod
    def close_peer(self, peer):
        """Drop one peer on purpose. Does not fire the disconnect callback."""

    @abstractmethod
    def close(self):
        """Close every socket."""

    def on_disconnect(self, callback):
        """callback(peer, reason) is called when a peer is lost. reason is one of
        'closed', 'reset', 'timeout', 'unreachable', 'protocol_error', 'send_buffer_full'."""
        self._disconnect_callback = callback

    def stats(self, peer=None):
        """Counters for the benchmark harness: totals, or one peer's."""
        return dict(self._totals if peer is None else self._per_peer.get(peer, {}))

    def _count(self, peer, name, amount=1):
        self._totals[name] += amount
        self._per_peer.setdefault(peer, Counter())[name] += amount

    def _report_lost(self):
        """Fire the callbacks for peers lost during the previous recv(). Delivering
        them one call late means the messages a peer sent just before it vanished
        (a BYE, say) are handled before its disconnect is."""
        lost, self._lost = self._lost, []
        for peer, reason in lost:
            if self._disconnect_callback is not None:
                self._disconnect_callback(peer, reason)
