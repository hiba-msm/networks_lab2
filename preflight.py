"""Check a machine before the demo.

    python preflight.py                                  this machine: Python, packages, fonts, port
    python preflight.py --public-test --host SERVER_IP   can this network reach the server, on TCP and on UDP?

--public-test needs both servers running on the server machine:
    python -m server --transport tcp --port 5055
    python -m server --transport udp --port 5055
It exits with status 1 if anything failed, and says which protocol is blocked.
"""

import argparse
import importlib
import os
import pathlib
import socket
import sys
import time

from common.constants import DEFAULT_PORT
from transport.udp_transport import PROBE_MAGIC

FONT_DIR = pathlib.Path(__file__).parent / "assets" / "fonts"
FONTS = ["SpaceGrotesk-Medium.ttf", "JetBrainsMono-Regular.ttf"]
TIMEOUT_S = 3.0


def check_tcp(host, port):
    """Returns (ok, what happened and what to do about it)."""
    try:
        started = time.monotonic()
        socket.create_connection((host, port), TIMEOUT_S).close()
    except socket.gaierror:
        return False, f"cannot resolve the host name {host!r}"
    except ConnectionRefusedError:
        # The machine answered "nothing listens here": the path is open.
        return False, ("REFUSED: the machine is reachable but nothing listens on this TCP port. "
                       "Start the server with --transport tcp, or check the port number.")
    except OSError:  # timed out, or no route
        return False, ("NO ANSWER: packets to this TCP port are being dropped. Open it in BOTH the "
                       f"cloud security group and the VM firewall (sudo ufw allow {port}/tcp).")
    return True, f"open (connected in {(time.monotonic() - started) * 1000:.0f} ms)"


def check_udp(host, port):
    """Send the probe the UDP server echoes. UDP has no handshake, so an echo is
    the only proof the port is open; no echo can mean several things."""
    try:
        address = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
    except socket.gaierror:
        return False, f"cannot resolve the host name {host!r}"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(1.2)  # the server answers one probe per second per IP, so retries are spaced out
        for _ in range(3):
            probe = PROBE_MAGIC + os.urandom(8)
            started = time.monotonic()
            try:
                sock.sendto(probe, address)
                reply, _ = sock.recvfrom(64)
            except socket.timeout:
                continue
            except ConnectionResetError:
                # Windows reports the ICMP "port unreachable" this way.
                return False, ("REFUSED: the machine is reachable but nothing listens on this UDP port. "
                               "Start the server with --transport udp, or check the port number.")
            if reply == probe:
                return True, f"open (echo in {(time.monotonic() - started) * 1000:.0f} ms)"
    return False, ("NO ANSWER after 3 probes. A blocked UDP port and a missing UDP server look the same: "
                   "silence. Check in this order: (1) the server runs with --transport udp; "
                   f"(2) the cloud security group has a UDP rule for {port}, not only TCP; "
                   f"(3) the VM firewall: sudo ufw allow {port}/udp; "
                   "(4) this network may filter UDP: try a phone hotspot, or run the server on 443/udp.")


def check_local(port):
    """Returns a list of (name, ok, detail)."""
    results = [("Python 3.11 or newer", sys.version_info >= (3, 11), sys.version.split()[0])]
    for module, purpose in (("pygame", "the client window"), ("qrcode", "the lobby QR code"),
                            ("matplotlib", "report charts only")):
        try:
            version = getattr(importlib.import_module(module), "__version__", "installed")
            results.append((f"{module} ({purpose})", True, version))
        except ImportError:
            results.append((f"{module} ({purpose})", False, "missing: pip install -r requirements.txt"))
    for font in FONTS:
        results.append((f"font {font}", (FONT_DIR / font).is_file(), str(pathlib.Path("assets") / "fonts" / font)))
    for kind, name in ((socket.SOCK_STREAM, "TCP"), (socket.SOCK_DGRAM, "UDP")):
        with socket.socket(socket.AF_INET, kind) as sock:
            try:
                sock.bind(("0.0.0.0", port))
                results.append((f"{name} port {port} free (matters on the server machine only)", True, "free"))
            except OSError as exc:
                results.append((f"{name} port {port} free (matters on the server machine only)", False,
                                f"in use: {exc.strerror}"))
    return results


def main():
    parser = argparse.ArgumentParser(description="Pre-demo checks")
    parser.add_argument("--public-test", action="store_true", help="test reachability of a running server")
    parser.add_argument("--host", help="server address (with --public-test)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    if args.public_test:
        if not args.host:
            parser.error("--public-test needs --host")
        results = [(f"{name} {args.host}:{args.port}", *check(args.host, args.port))
                   for name, check in (("TCP", check_tcp), ("UDP", check_udp))]
    else:
        results = check_local(args.port)

    for name, ok, detail in results:
        print(f"{'ok  ' if ok else 'FAIL'}  {name}: {detail}")
    failed = [name.split()[0] for name, ok, _ in results if not ok]
    if args.public_test:
        print("RESULT: " + (f"{' and '.join(failed)} blocked or not running" if failed else "both ports reachable"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
