#!/usr/bin/env python3
"""Prompt for and stage Fly secrets without putting them in shell history."""

import getpass
import re
import subprocess
import sys


NAMES = ("READWISE_TOKEN", "OPDS_USER", "OPDS_PASSWORD")


def collect_secrets() -> str:
    values = []
    for name in NAMES:
        value = getpass.getpass(f"{name}: ")
        # Restrict characters so dotenv parsing cannot change a secret's value.
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError(f"{name} must use only letters, numbers, underscores or hyphens")
        values.append(f"{name}={value}")
    return "\n".join(values) + "\n"


def main() -> int:
    try:
        secrets = collect_secrets()
        result = subprocess.run(
            ["flyctl", "secrets", "import", "--stage", "-a", "still-meadowstone-039"],
            input=secrets, text=True, check=False,
        )
        return result.returncode
    except (ValueError, OSError) as error:
        print(error, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
