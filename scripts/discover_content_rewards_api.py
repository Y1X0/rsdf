#!/usr/bin/env python3
"""Passive, sanitizing capture of Content Rewards' own network requests.

Run this while a REAL human (you) logs into contentrewards.com and browses
to a campaign list / video / download link, over a VNC view into a real,
headed Playwright-controlled Chromium window running in this Codespace (see
docs/CONTENT_REWARDS_CONNECTOR.md's Milestone 2 section for the exact VNC
setup). This script never defeats Cloudflare, a fingerprint check, or the
login flow itself -- it only *listens* to responses passing through the
browser it launches. If Cloudflare shows a challenge, you solve it yourself,
visually, in the VNC window, exactly like a normal visitor would.

Only run this against your own account. Nothing it captures is a secret:
- Header *names* are recorded, never values. Any header whose name contains
  cookie/auth/token/session/key/secret is flagged (still name-only) so you
  can tell at a glance which calls are authenticated.
- Request/response bodies are reduced to a type-shape (shape_of()) --
  never real field values (titles, ids, URLs, tokens all disappear; only
  the JSON structure and key names remain).

Output: JSONL lines appended to content_rewards_discovery.jsonl (repo root,
gitignored -- never committed). Read that file yourself, confirm it holds
no real values, and paste its contents back into chat. Never paste a raw
cookie/Authorization header value/access token here or anywhere else.
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "content_rewards_discovery.jsonl"
PROFILE_DIR = Path(__file__).resolve().parent.parent / ".content_rewards_browser_profile"

# Content Rewards is hosted on Whop, so its real API could live on either
# domain. Everything else (fonts, analytics, ads, unrelated CDNs) is ignored
# entirely -- nothing outside these two domains is ever written to disk.
DOMAIN_FILTER = ("contentrewards.com", "whop.com")

SENSITIVE_HEADER_MARKERS = ("cookie", "auth", "token", "session", "key", "secret")


def shape_of(value: object, _depth: int = 0) -> object:
    """Reduce a JSON value to its type-shape only -- dict keys (field
    names) are kept since that's exactly what Milestone 2 needs, but every
    leaf value is replaced by its type name so no real content ever reaches
    the output file."""
    if _depth > 6:
        return "..."
    if isinstance(value, dict):
        return {str(k): shape_of(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [shape_of(value[0], _depth + 1)] if value else []
    if value is None:
        return "null"
    return type(value).__name__


def header_names(headers: dict[str, str]) -> list[str]:
    return sorted(
        f"{name}{'[SENSITIVE]' if any(m in name.lower() for m in SENSITIVE_HEADER_MARKERS) else ''}"
        for name in headers
    )


def summarize_body(body_bytes: bytes | None, content_type: str) -> object:
    if not body_bytes:
        return {"content_type": content_type, "byte_length": 0}
    if "json" in content_type.lower():
        try:
            return {"content_type": content_type, "json_shape": shape_of(json.loads(body_bytes))}
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    return {"content_type": content_type, "byte_length": len(body_bytes)}


def is_interesting(url: str) -> bool:
    return any(domain in url for domain in DOMAIN_FILTER)


def main() -> None:
    OUTPUT_PATH.touch(exist_ok=True)
    count = 0

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        def on_response(response):
            nonlocal count
            request = response.request
            if request.resource_type not in ("xhr", "fetch"):
                return
            if not is_interesting(request.url):
                return

            entry = {
                "method": request.method,
                "url": request.url,
                "status": response.status,
                "request_headers": header_names(request.headers),
                "request_body": summarize_body(
                    request.post_data_buffer, request.headers.get("content-type", "")
                ),
                "response_headers": header_names(response.headers),
            }
            try:
                entry["response_body"] = summarize_body(
                    response.body(), response.headers.get("content-type", "")
                )
            except Exception as exc:
                entry["response_body"] = {"error": type(exc).__name__}

            with OUTPUT_PATH.open("a") as f:
                f.write(json.dumps(entry) + "\n")
            count += 1
            print(f"[captured #{count}] {entry['method']} {entry['url']} -> {entry['status']}")

        page.on("response", on_response)
        page.goto("https://contentrewards.com/discover")

        print("=" * 70)
        print("Browse the REAL Content Rewards site in the VNC window now:")
        print("  1. Log in normally if prompted -- solving Cloudflare's own")
        print("     challenge yourself, visually, is not a 'bypass'.")
        print("  2. Open your campaign/discover list.")
        print("  3. Open one campaign's video and its download link.")
        print(f"Matching requests are appended live to: {OUTPUT_PATH}")
        print("Press Ctrl+C in this terminal when you're done browsing.")
        print("=" * 70)

        try:
            while True:
                page.wait_for_timeout(1000)
        except KeyboardInterrupt:
            pass

        context.close()

    print(f"\nDone. {count} request(s) captured to {OUTPUT_PATH}")
    print("Open that file yourself, confirm it holds no real values, and")
    print("paste its contents back into chat.")


if __name__ == "__main__":
    main()
