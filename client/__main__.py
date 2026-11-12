"""Start the game:  python -m client --transport tcp|udp --host SERVER_IP --port 5055

This is the only client file that names a concrete transport.
"""

import argparse
import logging

from client.app import App
from common.constants import DEFAULT_PORT
from transport.tcp_transport import TcpTransport
from transport.udp_transport import UdpTransport

TRANSPORTS = {"tcp": TcpTransport, "udp": UdpTransport}

parser = argparse.ArgumentParser(description="Drag race client")
parser.add_argument("--transport", choices=sorted(TRANSPORTS), required=True)
parser.add_argument("--host", default="127.0.0.1", help="server address")
parser.add_argument("--port", type=int, default=DEFAULT_PORT)
parser.add_argument("--name", default="", help="pre-fills the name field")
parser.add_argument("--event-mode", action="store_true",
                    help="send individual press events instead of the cumulative count "
                         "(the naive design, kept to show how it loses presses under packet loss)")
args = parser.parse_args()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
App(TRANSPORTS[args.transport], args.transport.upper(), args.host, args.port, args.name, args.event_mode).run()
