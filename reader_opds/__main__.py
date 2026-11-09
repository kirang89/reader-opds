"""Run the Reader OPDS server."""

import argparse
import os

from .server import OPDSServer


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve Readwise Reader as an OPDS catalog")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="port (default: 8080)")
    args = parser.parse_args()
    token = os.environ.get("READWISE_TOKEN")
    if not token:
        parser.error("set READWISE_TOKEN to your Readwise access token")
    username, password = os.environ.get("OPDS_USER"), os.environ.get("OPDS_PASSWORD")
    if bool(username) != bool(password) or (os.environ.get("FLY_APP_NAME") and not (username and password)):
        # A public Fly deployment must not accidentally expose the catalog.
        parser.error("set both OPDS_USER and OPDS_PASSWORD (required on Fly)")
    server = OPDSServer((args.host, args.port), token=token,
                        basic_auth=(username, password) if username and password else None)
    print(f"Serving Reader OPDS on {args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
