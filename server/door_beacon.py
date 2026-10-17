#!/usr/bin/env python3
"""FriendlyFire door beacon: tiny HTTPS latency endpoint on the game VM.

Published via OPNsense NAT (WAN 443 -> VM 8443), it terminates the
BROWSER'S own route to the home door — the exact path a Minecraft
client takes when connecting to play.example.com:25565. The welcome page times
this beacon cross-origin, so it echoes CORS for trusted origins.

TLS is mandatory (mixed-content rule: the HTTPS page cannot time plain-
HTTP beacons). Obtain a Let's Encrypt cert for the DDNS hostname via
certbot HTTP-01 (NAT WAN 80 -> VM 80) and point the service at it.

Stdlib only; no third-party dependencies on the game VM.
"""
import argparse
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_origins: set = set()


class Beacon(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _cors_headers(self):
        origin = self.headers.get("Origin", "")
        if origin and origin in _origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def do_GET(self):
        if self.path != "/ping":
            self.send_error(404)
            return
        self.send_response(204)
        self.send_header("Cache-Control", "no-store")
        self._cors_headers()
        self.end_headers()

    def do_OPTIONS(self):
        # CORS preflight: browsers rarely preflight a simple GET, but
        # honour it anyway so exotic fetch setups still work.
        self.send_response(204)
        self.send_header("Cache-Control", "no-store")
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def log_message(self, format, *args):  # noqa: A002 (std-lib signature)
        pass  # quiet: journald noise control


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8443)
    ap.add_argument("--cert", required=True, help="PEM cert chain")
    ap.add_argument("--key", required=True, help="PEM private key")
    ap.add_argument("--origins", default="https://mc.example.com",
                    help="comma-separated CORS-allowed origins")
    args = ap.parse_args()
    _origins.update(o.strip() for o in args.origins.split(",") if o.strip())

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(args.cert, args.key)

    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Beacon)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    srv.daemon_threads = True
    srv.serve_forever()


if __name__ == "__main__":
    main()
