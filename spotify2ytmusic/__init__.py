#!/usr/bin/env python3

import os


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=value lines from `path` into os.environ.

    Existing environment variables take precedence. Values are taken verbatim
    after the first "=" (so cookies containing "=" and ";" are preserved),
    optionally wrapped in matching single or double quotes.
    """
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if value:
                os.environ.setdefault(key.strip(), value)


load_dotenv()

from . import cli  # noqa: E402

