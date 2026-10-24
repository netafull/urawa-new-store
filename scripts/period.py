#!/usr/bin/env python3
"""開店時期の正規化。求人の文言(開店語を含む句)を読み、表示用の文字列・精度・
開始日(ISO)・推定フラグ・「すでに開店済みか」を返す。入力の文言は保存しない
(返すのは正規化した値だけ)。純粋関数。

規則:
- 年つき(西暦・令和・平成)は書かれたとおり。年なしの月・日は「実行日から見て」補う:
  今月以降 → 今年 / 過去2か月以内(例: 実行日が10月なら8・9月)→ 開店済みの可能性(past) /
  それより前の月 → 翌年に補い、推定フラグ(表示は「（推定）」つき)
- 季節語: 春=3〜5月 夏=6〜8月 秋=9〜11月 冬=12〜2月。start は季節の最初の日(冬は12/1)
- 「今秋」は今年、「来春」は来年。精度は day / ten_days / month / season / year / unknown
- 開店済み(past)の判定: 年つきの日付が実行日の2か月以上前 / 「オープンした」等の過去表現
"""

from __future__ import annotations

import calendar
import datetime
import re
import unicodedata

RANK = {"day": 5, "ten_days": 4, "month": 3, "season": 2, "year": 1, "unknown": 0}
UNKNOWN_LABEL = "時期不明（開店予定の募集あり）"
SEASON_START = {"春": 3, "夏": 6, "秋": 9, "冬": 12}
DEC_START = {"上旬": 1, "初旬": 1, "中旬": 11, "中頃": 11, "下旬": 21}
DEC_LABEL = {"上旬": "上旬", "初旬": "上旬", "中旬": "中旬", "中頃": "中旬", "下旬": "下旬"}

_YEAR = r"(?:(?P<era>令和|平成|R|H)(?P<ey>元|\d{1,2})|(?P<wy>(?:19|20)\d{2}))年"
_DEC = r"(?P<dec>上旬|初旬|中旬|中頃|下旬)"
FULL_RE = re.compile(_YEAR + r"(?P<m>\d{1,2})月(?:(?P<d>\d{1,2})日|" + _DEC + r")?")
YEAR_SEASON_RE = re.compile(_YEAR + r"(?P<s>春|夏|秋|冬)")
YEAR_ONLY_RE = re.compile(_YEAR + r"(?!以内|間|度|半|目|前|ぶり)")
SEASON_RE = re.compile(r"(?P<k>今|来)(?P<s>春|夏|秋|冬)")
MONTH_RE = re.compile(r"(?<![\d年])(?P<m>\d{1,2})月(?:(?P<d>\d{1,2})日|" + _DEC + r")?")
SLASH_RE = re.compile(r"(?<![\d/])(?P<m>\d{1,2})/(?P<d>\d{1,2})(?![\d/])")
NEXT_MONTH_RE = re.compile(r"(?P<k>来月|今月)")
PAST_RE = re.compile(r"(?:オープン|OPEN|開院|開園|開校|開業)(?:した|しました|いたしました|済み?|して(?:い|お))", re.I)


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").replace(" ", "")


def _year_of(m) -> int | None:
    if m.group("wy"):
        return int(m.group("wy"))
    n = 1 if m.group("ey") == "元" else int(m.group("ey"))
    return (2018 if m.group("era") in ("令和", "R") else 1988) + n


def _minus_months(d: datetime.date, n: int) -> datetime.date:
    idx = d.year * 12 + d.month - 1 - n
    y, mo = divmod(idx, 12)
    mo += 1
    return datetime.date(y, mo, min(d.day, calendar.monthrange(y, mo)[1]))


def _result(precision, start, display_core, estimated=False, past=False) -> dict:
    return {
        "period": display_core + ("（推定）" if estimated else ""),
        "precision": precision,
        "start": start.isoformat() if start else None,
        "estimated": estimated,
        "past": past,
    }


def _mk(precision: str, y: int, m: int | None, d: int | None, dec: str | None, season: str | None,
        today: datetime.date, estimated: bool = False, explicit_year: bool = True, force_past: bool = False) -> dict:
    cut = _minus_months(today, 2)
    if precision == "day":
        start = datetime.date(y, m, d)
        core = f"{y}年{m}月{d}日"
        past = start < cut
    elif precision == "ten_days":
        start = datetime.date(y, m, DEC_START[dec])
        core = f"{y}年{m}月{DEC_LABEL[dec]}"
        past = start < cut
    elif precision == "month":
        start = datetime.date(y, m, 1)
        core = f"{y}年{m}月"
        past = (y * 12 + m) < (today.year * 12 + today.month) - 2
    elif precision == "season":
        start = datetime.date(y, SEASON_START[season], 1)
        core = f"{y}年{season}"
        past = start < cut
    else:  # year
        start = datetime.date(y, 1, 1)
        core = f"{y}年"
        past = datetime.date(y, 12, 31) < cut
    return _result(precision, start, core, estimated, past or force_past)


def normalize_open_period(text: str, today: datetime.date) -> dict:
    """開店語を含む句から開店時期を正規化する。
    {period, precision, start(ISO|None), estimated, past}。読めなければ precision=unknown。"""
    t = _norm(text)
    force_past = bool(PAST_RE.search(t))

    m = FULL_RE.search(t)
    if m:
        y, mo = _year_of(m), int(m.group("m"))
        if 1 <= mo <= 12:
            if m.group("d") and 1 <= int(m.group("d")) <= calendar.monthrange(y, mo)[1]:
                return _mk("day", y, mo, int(m.group("d")), None, None, today, force_past=force_past)
            if m.group("dec"):
                return _mk("ten_days", y, mo, None, m.group("dec"), None, today, force_past=force_past)
            return _mk("month", y, mo, None, None, None, today, force_past=force_past)
    m = YEAR_SEASON_RE.search(t)
    if m:
        return _mk("season", _year_of(m), None, None, None, m.group("s"), today, force_past=force_past)
    m = SEASON_RE.search(t)
    if m:
        y = today.year + (1 if m.group("k") == "来" else 0)
        return _mk("season", y, None, None, None, m.group("s"), today, force_past=force_past)
    # 年なしの月・日
    m = MONTH_RE.search(t)
    sm = None if m else SLASH_RE.search(t)
    nm = None if (m or sm) else NEXT_MONTH_RE.search(t)
    if m or sm:
        src = m or sm
        mo = int(src.group("m"))
        d = int(src.group("d")) if src.group("d") else None
        dec = src.groupdict().get("dec")
        if 1 <= mo <= 12 and (d is None or 1 <= d <= 31):
            prec = "day" if d else ("ten_days" if dec else "month")
            return _infer(prec, mo, d, dec, today, force_past)
    if nm:
        mo = today.month + (1 if nm.group("k") == "来月" else 0)
        y = today.year + (1 if mo > 12 else 0)
        return _mk("month", y, (mo - 1) % 12 + 1, None, None, None, today, force_past=force_past)
    m = YEAR_ONLY_RE.search(t)
    if m:
        return _mk("year", _year_of(m), None, None, None, None, today, force_past=force_past)
    r = _result("unknown", None, UNKNOWN_LABEL)
    r["past"] = force_past
    return r


def _infer(prec: str, mo: int, d: int | None, dec: str | None, today: datetime.date, force_past: bool) -> dict:
    """年のない月・日を実行日から補う。"""
    y = today.year
    try:
        if prec == "month":
            back = (today.year * 12 + today.month) - (y * 12 + mo)  # 何か月前か(負なら先)
            if back <= 0:
                return _mk("month", y, mo, None, None, None, today, force_past=force_past)
            if back <= 2:
                r = _mk("month", y, mo, None, None, None, today, force_past=True)
                return r
            return _mk("month", y + 1, mo, None, None, None, today, estimated=True, force_past=force_past)
        day = d if prec == "day" else DEC_START[dec]
        cand = datetime.date(y, mo, min(day, calendar.monthrange(y, mo)[1]))
        cut = _minus_months(today, 2)
        if cand >= today:
            return _mk(prec, y, mo, d, dec, None, today, force_past=force_past)
        if cand >= cut:
            return _mk(prec, y, mo, d, dec, None, today, force_past=True)
        return _mk(prec, y + 1, mo, d, dec, None, today, estimated=True, force_past=force_past)
    except ValueError:
        r = _result("unknown", None, UNKNOWN_LABEL)
        r["past"] = force_past
        return r


def better(new: dict, old: dict) -> bool:
    """new の精度が old より高いか。"""
    return RANK[new["precision"]] > RANK.get(old.get("open_period_precision", "unknown"), 0)
