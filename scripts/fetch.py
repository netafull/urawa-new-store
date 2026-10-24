#!/usr/bin/env python3
"""HTTP取得の共通部分。自己識別型のUser-Agentで取りに行き、403/429など明確に
ブロックされた場合は、UA偽装などの回避策には切り替えず BlockedError で呼び出し側に
伝える(その社の取得を止める)。robots.txtの禁止パスは取らない。
全リクエストの間隔(既定6秒)と1実行あたりの上限(既定20)もここで守る。
"""

from __future__ import annotations

import re
import time
from urllib.parse import urlsplit

import requests

TIMEOUT = 20


class BlockedError(Exception):
    """403/429等、明確にブロックされたと判断できる応答。"""


class ForbiddenPathError(Exception):
    """robots.txt または自前の禁止リストにより取らないパス。"""


class BudgetExceededError(Exception):
    """1実行あたりのリクエスト上限に達した。"""


def parse_robots_disallow(text: str) -> list[str]:
    """User-agent: * のグループの Disallow パターンを返す(Allowは見ない。
    取れる範囲を狭める側に倒すため、Disallowだけで判定する)。"""
    rules: list[str] = []
    applies = False
    in_group_header = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (s.strip() for s in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if not in_group_header:
                applies = False
            in_group_header = True
            if value == "*":
                applies = True
        else:
            in_group_header = False
            if key == "disallow" and applies and value:
                rules.append(value)
    return rules


def _pattern_to_regex(pattern: str) -> re.Pattern:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    rx = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
    return re.compile("^" + rx + ("$" if anchored else ""))


def is_path_forbidden(path_and_query: str, patterns: list[str]) -> bool:
    return any(_pattern_to_regex(p).match(path_and_query) for p in patterns)


class Fetcher:
    def __init__(self, user_agent: str, interval: float, max_requests: int):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.interval = interval
        self.max_requests = max_requests
        self.count = 0
        self._last = None

    def _wait(self) -> None:
        if self._last is not None:
            elapsed = time.monotonic() - self._last
            if elapsed < self.interval:
                time.sleep(self.interval - elapsed)
        self._last = time.monotonic()

    def get(self, url: str, forbidden: list[str] | None = None) -> str:
        parts = urlsplit(url)
        target = parts.path + (("?" + parts.query) if parts.query else "")
        if forbidden and is_path_forbidden(target, forbidden):
            raise ForbiddenPathError(url)
        if self.count >= self.max_requests:
            raise BudgetExceededError(url)
        self._wait()
        self.count += 1
        res = self.session.get(url, timeout=TIMEOUT)
        if res.status_code in (403, 429):
            raise BlockedError(f"HTTP {res.status_code}: {url}")
        res.raise_for_status()
        res.encoding = res.encoding or "utf-8"
        if "charset" not in res.headers.get("content-type", "").lower():
            res.encoding = "utf-8"
        return res.text


def snapshot_label(url: str) -> str:
    """保存したHTMLのファイル名(URL末尾80文字を英数字化したもの)。保存側と読み込み側で共通。"""
    from urllib.parse import unquote
    return re.sub(r"[^\w\-]+", "_", unquote(url.split("://", 1)[1]))[-80:]


class OfflineFetcher:
    """ネットワークに出ない代替。URAWA_SAVE_HTML_DIR で保存した一覧HTML(<dir>/baitoru/,
    <dir>/kyujinbox/)と、任意で詳細HTML(<dir>/baitoru_detail/<求人ID>.html)を読む。
    robots.txt は <dir>/robots_<site>.txt があれば読み、無ければ取得失敗として扱う
    (自前の禁止リストだけで動く)。禁止パスの判定は通常どおり行う。"""

    def __init__(self, directory: str):
        from pathlib import Path
        self.dir = Path(directory)
        self.count = 0  # ネットワークリクエストは常に0

    def get(self, url: str, forbidden: list[str] | None = None) -> str:
        parts = urlsplit(url)
        target = parts.path + (("?" + parts.query) if parts.query else "")
        if forbidden and is_path_forbidden(target, forbidden):
            raise ForbiddenPathError(url)
        site = "baitoru" if "baitoru.com" in parts.netloc else "kyujinbox"
        if parts.path == "/robots.txt":
            f = self.dir / f"robots_{site}.txt"
        elif parts.path.startswith("/jobdetail/"):
            f = self.dir / "baitoru_detail" / f"{parts.path.strip('/').split('/')[-1]}.html"
        else:
            f = self.dir / site / f"{snapshot_label(url)}.html"
        return f.read_text(encoding="utf-8")  # 無ければ FileNotFoundError(通常の失敗として扱われる)
