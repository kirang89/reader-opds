# Reader OPDS

Browse saved [Readwise Reader](https://readwise.io/read) articles in an OPDS client and download them as EPUBs. Requires Python 3.12 or later; no third-party packages.

## Run locally

Get a [Readwise access token](https://readwise.io/access_token), then run:

```sh
export READWISE_TOKEN='your-token'       # Required
export OPDS_USER='your-username'        # Optional; set both for authentication
export OPDS_PASSWORD='your-password'    # Optional; set both for authentication
python3 -m reader_opds --host 0.0.0.0 --port 8080
```

Add `http://YOUR_COMPUTER_LAN_IP:8080/opds` to your OPDS client. The server must remain running and its firewall must allow the port.

Without credentials, anyone who can reach the server can read your articles. Basic Auth over HTTP does not encrypt credentials or content. Use a trusted network for local access; for public access use HTTPS and a long, unique password. Avoid putting real credentials in shell history or committed files.

## Deploy to Fly.io

Install and sign in to [flyctl](https://fly.io/docs/flyctl/install/). Create your own app, then replace the existing app name in **both** `fly.toml` and `scripts/set_fly_auth.py` with your new app name. Optionally change `primary_region` in `fly.toml`.

```sh
flyctl apps create YOUR_APP_NAME
python3 scripts/set_fly_auth.py  # Prompt for and stage all three secrets
flyctl deploy
```

The script accepts letters, numbers, underscores and hyphens. It stages secrets for the app named in the script; always check that name before running it. Fly requires both OPDS credentials. Add `https://YOUR_APP_NAME.fly.dev/opds` to your client. Fly may charge for hosting; check its current pricing.

Use the HTTPS URL directly: redirecting from HTTP cannot protect credentials already sent over HTTP. The server limits active connections to 32, closes idle connections after 10 seconds, and throttles authentication after 10 failed attempts in 60 seconds. This limit is shared across clients on each process, resets on restart, and does not replace network-level abuse protection.

## Limitations

Articles use Reader's exported `html_content`; documents without content may produce empty books. Images and complex formatting are omitted. Reading progress and highlights do not sync. Readwise API rate limits apply.

## Test

```sh
python3 -m unittest discover -s tests -v
```

## License

[MIT](LICENSE)
