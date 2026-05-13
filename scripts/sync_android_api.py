#!/usr/bin/env python3
"""Fetch OpenAPI JSON from a running Sirena tablet gateway for Android codegen.

Usage (Jetson or dev host with Sirena UI + gateway running):

  python scripts/sync_android_api.py --url http://127.0.0.1:8787

Writes ``android/app/src/main/assets/nina-openapi.json`` (creates parent dirs).

Optional: install openapi-generator-cli and generate Kotlin stubs, e.g.::

  openapi-generator-cli generate -i nina-openapi.json -g kotlin -o /tmp/out
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--url",
        default="http://127.0.0.1:8787",
        help="Base URL of tablet gateway (default %(default)s)",
    )
    p.add_argument(
        "--out",
        default="",
        help="Output path (default: android/app/src/main/assets/nina-openapi.json under repo root)",
    )
    args = p.parse_args()
    repo = Path(__file__).resolve().parents[1]
    out = Path(args.out) if args.out else repo / "android" / "app" / "src" / "main" / "assets" / "nina-openapi.json"
    src = (args.url.rstrip("/")) + "/openapi.json"
    try:
        with urllib.request.urlopen(src, timeout=30) as resp:
            raw = resp.read()
        data = json.loads(raw.decode("utf-8"))
    except urllib.error.URLError as e:
        print(f"Failed to fetch {src}: {e}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"Invalid JSON from {src}: {e}", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
