#!/usr/bin/env python3
"""Publish drafts whose episodes have all finished transcoding.

Runs alongside a long upload batch so finished dramas go live immediately
instead of waiting for the final publish pass. A drama is only touched when
every episode row is in ``in_review`` or ``published``, so partially uploaded
titles stay hidden.

Usage::

    python3 scripts/publish_ready_dramas.py --min-id 116 --loop 180
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API_DEFAULT = "https://api.minitv.us"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


def api(token: str, base: str, method: str, path: str, payload: dict | None = None) -> dict:
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}") from exc


def login(base: str) -> str:
    body = json.dumps({
        "username": os.environ.get("ADMIN_USER", "admin"),
        "password": os.environ.get("ADMIN_PASS", "admin-1234"),
    }).encode()
    request = urllib.request.Request(
        base + "/admin/v1/auth/login",
        data=body,
        headers={"User-Agent": UA, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())["token"]


def publish_ready(token: str, base: str, min_id: int) -> list[str]:
    published: list[str] = []
    page = 1
    while True:
        listing = api(token, base, "GET", f"/admin/v1/dramas?status=draft&page={page}&page_size=100")
        items = [item for item in listing.get("items", []) if item["id"] >= min_id]
        if not items:
            break
        for drama in items:
            rows = api(token, base, "GET", f"/admin/v1/dramas/{drama['id']}/episodes?page_size=200")["items"]
            if not rows:
                continue
            if any(row["status"] not in {"in_review", "published"} for row in rows):
                continue
            for row in rows:
                if row["status"] == "in_review":
                    try:
                        api(token, base, "POST", f"/admin/v1/episodes/{row['id']}/publish", {})
                    except RuntimeError as exc:
                        print(f"  ep{row['episode_number']:03d} publish failed: {exc}")
            try:
                api(token, base, "POST", f"/admin/v1/dramas/{drama['id']}/status", {"to_status": "in_review"})
                api(token, base, "POST", f"/admin/v1/dramas/{drama['id']}/status", {"to_status": "published"})
                published.append(drama["title"])
                print(f"published #{drama['id']} {drama['title']} ({len(rows)} episodes)")
            except RuntimeError as exc:
                print(f"drama #{drama['id']} publish failed: {exc}")
        if len(listing.get("items", [])) < 100:
            break
        page += 1
    return published


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default=os.environ.get("MINITV_API", API_DEFAULT))
    parser.add_argument("--min-id", type=int, default=116)
    parser.add_argument("--loop", type=int, default=0, help="seconds between passes; 0 runs once")
    args = parser.parse_args()

    while True:
        try:
            token = login(args.api)
            stamp = datetime.datetime.now().strftime("%H:%M:%S")
            published = publish_ready(token, args.api, args.min_id)
            if not published:
                print(f"[{stamp}] nothing ready")
        except Exception as exc:
            print(f"[{datetime.datetime.now():%H:%M:%S}] pass failed: {exc}")
        if not args.loop:
            return
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
