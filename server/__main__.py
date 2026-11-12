"""Start the server:  python -m server --transport tcp|udp --host 0.0.0.0 --port 5055

This is the only server file that names a concrete transport.
"""

import argparse
import logging

from common.constants import DEFAULT_PORT, TRACK_LENGTH
from server.server import GameServer
from transport.tcp_transport import TcpTransport
from transport.udp_transport import UdpTransport

TRANSPORTS = {"tcp": TcpTransport, "udp": UdpTransport}

parser = argparse.ArgumentParser(description="Drag race server")
parser.add_argument("--transport", choices=sorted(TRANSPORTS), required=True)
parser.add_argument("--host", default="0.0.0.0", help="address to bind (default: all interfaces)")
parser.add_argument("--port", type=int, default=DEFAULT_PORT)
parser.add_argument("--track-length", type=float, default=TRACK_LENGTH)
args = parser.parse_args()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
transport = TRANSPORTS[args.transport].listen(args.host, args.port)
logging.info("%s server listening on %s:%s", args.transport.upper(), args.host, args.port)
try:
    GameServer(transport, track_length=args.track_length).run()
except KeyboardInterrupt:
    logging.info("stopped by Ctrl+C")
finally:
    transport.close()
