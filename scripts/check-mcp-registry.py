#!/usr/bin/env python3
"""Confirm an exact Kindex release in the official MCP Registry."""

import argparse
import json
import math
import time
import urllib.error
import urllib.request


SERVER_NAME = "io.github.wandercom/kindex"
REGISTRY_URL = (
    "https://registry.modelcontextprotocol.io/v0.1/servers/"
    "io.github.wandercom%2Fkindex/versions/latest"
)
OFFICIAL_META = "io.modelcontextprotocol.registry/official"


def _matches(document: object, version: str) -> bool:
    if not isinstance(document, dict):
        return False
    server = document.get("server")
    metadata = document.get("_meta")
    if not isinstance(server, dict) or not isinstance(metadata, dict):
        return False
    official = metadata.get(OFFICIAL_META)
    return (
        isinstance(official, dict)
        and official.get("isLatest") is True
        and server.get("name") == SERVER_NAME
        and server.get("version") == version
    )


def verify(tag: str, *, attempts: int = 8, delay: float = 5.0,
           timeout: float = 15.0) -> None:
    """Retry transient failures and stale entries, failing after a fixed bound."""
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts <= 0:
        raise RuntimeError("attempts must be a positive integer")
    for name, value in (("delay", delay), ("timeout", timeout)):
        try:
            valid = (
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(value)
                and (value > 0 if name == "timeout" else value >= 0)
            )
        except OverflowError:
            valid = False
        if not valid:
            bound = "positive" if name == "timeout" else "nonnegative"
            raise RuntimeError(f"{name} must be finite and {bound}")
    if not isinstance(tag, str) or not tag.removeprefix("v").strip():
        raise RuntimeError("a release tag is required")
    version = tag.removeprefix("v")
    last_failure = "no matching latest entry"
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(REGISTRY_URL, timeout=timeout) as response:
                document = json.load(response)
            if _matches(document, version):
                print(f"{SERVER_NAME} now serves {version}")
                return
            last_failure = "registry response is stale, missing, or malformed"
        except urllib.error.HTTPError as exc:
            if exc.code not in (404, 429) and not 500 <= exc.code <= 599:
                raise RuntimeError(f"Registry verification refused: HTTP {exc.code}") from exc
            last_failure = f"HTTP {exc.code}"
        except (TimeoutError, urllib.error.URLError) as exc:
            last_failure = f"{type(exc).__name__}: {exc}"
        except (ValueError, UnicodeError) as exc:
            last_failure = f"malformed registry response: {type(exc).__name__}"
        print(f"{SERVER_NAME} {version} not confirmed (attempt {attempt}/{attempts}): {last_failure}")
        if attempt < attempts:
            time.sleep(delay)
    raise RuntimeError(
        f"{SERVER_NAME} {version} not confirmed after {attempts} attempts: {last_failure}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="Release tag, for example v0.48.1")
    args = parser.parse_args()
    try:
        verify(args.tag)
    except RuntimeError as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
