#!/usr/bin/env python3
"""求人サイトの一覧ページHTMLから、判定に使う最小限の項目を取り出す。
ネットワークには触らない(テストしやすいように fetch とは分けてある)。

返す dict の text 系(title/snippet)は判定に使うだけで、保存はしない。
{site, store_field, title, snippet, area, source_index}
"""

from __future__ import annotations

import json
import re
import unicodedata

from bs4 import BeautifulSoup


def nfkc(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def _by_prefix(node, prefix: str):
    """バイトルのクラス名は _title_w12ll_157 のようにビルドごとに変わるため、前方一致で探す。"""
    return node.find(class_=re.compile(r"^" + re.escape(prefix) + r"_"))


def parse_baitoru_list(html: str) -> list[dict]:
    """バイトルの一覧。/jobdetail/ へのカード(_cardLink_)を持つものだけ。
    ページ下部の「あなたにおすすめ」枠(pname=recom_...)は他地域の求人なので除く。"""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for a in soup.find_all("a", class_=re.compile(r"^_cardLink_"), href=re.compile(r"/jobdetail/")):
        if "pname=recom_" in a["href"]:
            continue
        card = a.parent
        store = _by_prefix(card, "_storeName")
        title = _by_prefix(card, "_title")
        labeled = card.find_all(class_=re.compile(r"^_locationRow_"))
        area = ""
        if labeled:
            pick = next((r for r in labeled if "勤務" in r.get_text()), labeled[0])
            val = _by_prefix(pick, "_locationValue")
            area = val.get_text(strip=True) if val else ""
        else:
            d = _by_prefix(card, "_detailText")
            area = d.get_text(strip=True) if d else ""
        is_pr = any(t.get_text(strip=True) == "PR" for t in card.find_all(class_=re.compile(r"^_text_")))
        jid = re.search(r"/jobdetail/(\d+)", a["href"])
        items.append({
            "site": "baitoru",
            "job_id": jid.group(1) if jid else "",  # 詳細取得にだけ使う。保存しない
            "store_field": nfkc(store.get_text()) if store else "",
            "title": nfkc(title.get_text()) if title else "",
            "snippet": "",
            "area": nfkc(area),
            "is_pr": is_pr,
        })
    return items


def parse_kyujinbox_list(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for card in soup.select("section.p-result_card"):
        def text(sel: str) -> str:
            el = card.select_one(sel)
            return nfkc(el.get_text(" ", strip=True)) if el else ""
        items.append({
            "site": "kyujinbox",
            "store_field": text(".p-result_companyName"),
            "title": text(".p-result_title_link"),
            "snippet": text(".p-result_lines"),
            "area": text(".p-result_area"),
            "is_pr": False,
        })
    return items


def kyujinbox_has_next(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    return soup.select_one('a[href*="pg="]') is not None


_DETAIL_TITLE_RE = re.compile(r"^【(.+?)】")
_DETAIL_STATION_RE = re.compile(r"\s(\S+?)\s*(?:徒歩|バス|車)")


def parse_baitoru_detail(html: str) -> dict:
    """バイトルの求人詳細から、店名・住所・最寄駅・掲載開始日・開店語を含む句の元になる
    見出しだけを取り出す。本文・給与・画像は返さない(開店時期の句は rules 側で
    見出しとJSON-LDの説明から30文字以内に切る。説明の全文は呼び出し側に渡さない)。
    {store_title, locality, street, station, date_posted, open_text}"""
    soup = BeautifulSoup(html, "html.parser")
    out = {"store_title": "", "locality": "", "street": "", "station": "", "date_posted": "", "open_text": ""}
    t = soup.title.get_text() if soup.title else ""
    m = _DETAIL_TITLE_RE.match(nfkc(t))
    if m:
        out["store_title"] = m.group(1)
    job = None
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        if isinstance(d, dict) and d.get("@type") == "JobPosting":
            job = d
            break
    heads = " ".join(h.get_text(" ", strip=True) for h in soup.find_all("h2")[:2])
    desc = ""
    if job:
        out["date_posted"] = str(job.get("datePosted", ""))[:10]
        addr = ((job.get("jobLocation") or {}).get("address") or {}) if isinstance(job.get("jobLocation"), dict) else {}
        out["locality"] = nfkc(addr.get("addressLocality", ""))
        street = nfkc(addr.get("streetAddress", ""))
        out["street"] = re.split(r"[\d\s]", street, maxsplit=1)[0]  # 町名だけ(番地・ビル名は持たない)
        desc = BeautifulSoup(job.get("description", ""), "html.parser").get_text("\n")
        sm = re.search(r"最寄り駅\s*\n?(.+)", desc)
        if sm:
            first = sm.group(1).split("、")[0]
            st = _DETAIL_STATION_RE.search(" " + nfkc(first))
            if st:
                out["station"] = st.group(1)
    # 開店時期の句探しに使う素材は、見出しと説明の中の「開店語を含む行」だけに絞る
    lines = [nfkc(x) for x in desc.splitlines() if re.search(r"オープン|OPEN|開園|開校|開業|開院|新店", x, re.I)]
    out["open_text"] = " / ".join([nfkc(heads)] + lines[:5])
    return out
