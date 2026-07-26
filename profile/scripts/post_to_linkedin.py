#!/usr/bin/env python3
"""
Posts new entries from profile.yaml's `updates:` list to LinkedIn via the
Posts API (https://api.linkedin.com/rest/posts), which replaced the legacy
ugcPosts endpoint in 2024.

Env vars required:
  LINKEDIN_ACCESS_TOKEN  - member access token with w_member_social scope.
                           No refresh token is issued (app isn't on LinkedIn's
                           Marketing Developer Platform) -- expires 60 days
                           after issuance and must be regenerated manually via
                           the LinkedIn Developer Portal's token generator.
  LINKEDIN_AUTHOR_URN    - e.g. urn:li:person:JKoKTJIwxu (not secret, just an ID)

Tracks which updates have already been posted in updates_posted.json
(sitting alongside profile.yaml) so re-running this script never double-posts.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = ROOT / "profile.yaml"
POSTED_STATE_PATH = ROOT / "updates_posted.json"

LINKEDIN_VERSION = "202506"  # LinkedIn-Version header, format YYYYMM
POSTS_URL = "https://api.linkedin.com/rest/posts"


def update_key(update: dict) -> str:
    raw = f"{update.get('date', '')}|{update.get('title', '')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def load_posted_state() -> set:
    if POSTED_STATE_PATH.exists():
        return set(json.loads(POSTED_STATE_PATH.read_text()))
    return set()


def save_posted_state(posted: set) -> None:
    POSTED_STATE_PATH.write_text(json.dumps(sorted(posted), indent=2) + "\n")


def build_commentary(update: dict) -> str:
    parts = [update["title"]]
    if update.get("summary"):
        parts.append(update["summary"])
    if update.get("url"):
        parts.append(update["url"])
    return "\n\n".join(parts)


def post_update(update: dict, token: str, author_urn: str) -> None:
    body = {
        "author": author_urn,
        "commentary": build_commentary(update),
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    resp = requests.post(
        POSTS_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
            "LinkedIn-Version": LINKEDIN_VERSION,
        },
        json=body,
        timeout=30,
    )
    if resp.status_code != 201:
        raise RuntimeError(f"LinkedIn post failed ({resp.status_code}): {resp.text}")
    post_id = resp.headers.get("x-restli-id", "<unknown>")
    print(f"Posted: {update['title']!r} -> {post_id}")


def main() -> int:
    token = os.environ.get("LINKEDIN_ACCESS_TOKEN")
    author_urn = os.environ.get("LINKEDIN_AUTHOR_URN")
    if not token or not author_urn:
        print("Missing LINKEDIN_ACCESS_TOKEN or LINKEDIN_AUTHOR_URN", file=sys.stderr)
        return 1

    profile = yaml.safe_load(PROFILE_PATH.read_text())
    updates = profile.get("updates") or []
    posted = load_posted_state()

    new_count = 0
    for update in updates:
        key = update_key(update)
        if key in posted:
            continue
        post_update(update, token, author_urn)
        posted.add(key)
        new_count += 1

    save_posted_state(posted)
    print(f"Done. {new_count} new update(s) posted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
