#!/usr/bin/env python3
"""さいたま市浦和区の「新規オープン店舗」を求人サイトの一覧ページから検出し、
data/stores.json に1店舗1レコードで蓄積する(公開サイトは作らない)。

取得元はバイトル(新着順の一覧)と求人ボックス(キーワード別の一覧)だけ。
求人詳細ページには行かない。求人の本文・タイトル全文・給与・詳細URLは保存せず、
求人の文言(抜粋を含む)は一切書き出さず、店名・勤務地・正規化した開店時期・浦和区の根拠・
出典・初回/最終確認日だけを残す。判定のために文言を読むのはメモリ内だけ。

再実行すると既存レコードを更新する(first_seen は保持)。
サイトごとにこけても(403等)、他サイトの処理は続行する。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import parsers  # noqa: E402
import period  # noqa: E402
import rules  # noqa: E402
from fetch import (BlockedError, BudgetExceededError, Fetcher, OfflineFetcher,  # noqa: E402
                   ForbiddenPathError, is_path_forbidden, parse_robots_disallow, snapshot_label)

CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA = ROOT / "data"
STORES_PATH = DATA / "stores.json"
SUMMARY_PATH = DATA / "run_summary.json"
JST = datetime.timezone(datetime.timedelta(hours=9))


def run_today() -> datetime.date:
    return datetime.datetime.now(JST).date()


def today() -> str:
    return datetime.datetime.now(JST).date().isoformat()


def now_iso() -> str:
    return datetime.datetime.now(JST).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 一覧ページのURL
# ---------------------------------------------------------------------------

def baitoru_urls(max_pages: int) -> list[str]:
    base = CONFIG["baitoru"]["list_url"]
    return [base] + [f"{base}page{n}/" for n in range(2, max_pages + 1)]


def kyujinbox_url(keyword: str, page: int = 1) -> str:
    cfg = CONFIG["kyujinbox"]
    path = quote(f"{keyword}の仕事-{cfg['area']}", safe="")
    url = f"{cfg['base_url']}/{path}"
    return url if page == 1 else f"{url}?pg={page}"


# ---------------------------------------------------------------------------
# 1件ぶんの判定 → レコード候補
# ---------------------------------------------------------------------------

def classify_item(item: dict, list_url: str, stats: dict) -> dict | None:
    """一覧の1件を判定する。新店候補(または区不明)ならレコード候補を返し、
    それ以外は None(理由は stats に数える)。"""
    stats["items_parsed"] += 1
    head = f"{item['title']} {item['store_field']}"
    in_head = rules.has_open_signal(head)
    if not in_head and not rules.has_open_signal(item["snippet"]):
        stats["no_open_signal"] += 1
        return None
    texts = [item["title"], item["store_field"], item["snippet"]]
    excerpt = rules.extract_excerpt(*texts)  # 判定用の一時値(保存しない)
    # 求人ボックスのスニペットは定型句が多い。タイトル/店名欄に開店語が無く、
    # スニペットだけの場合は、開店時期(月・予定など)が読めるものだけ拾う。
    if not in_head and not rules.DATE_TOKEN_RE.search(excerpt) and not rules.has_store_specific_new(item["snippet"]):
        stats["snippet_only_undated"] += 1
        return None
    # 開店時期を正規化する(月日の読み取りは30文字に切る前の句全体で行う)。開店済みなら除外
    op = rules.open_period_from(texts, run_today())
    if op["past"]:
        stats["excluded"]["already_open"] = stats["excluded"].get("already_open", 0) + 1
        return None
    full = rules.extract_excerpt(*texts, limit=None)
    if rules.other_area_in_text(full):
        stats["excluded"]["other_area_in_text"] = stats["excluded"].get("other_area_in_text", 0) + 1
        return None
    reason = rules.exclusion_reason(item["title"], item["store_field"], excerpt)
    if reason:
        stats["excluded"][reason] = stats["excluded"].get(reason, 0) + 1
        return None
    store_name = rules.extract_store_name(item["store_field"])
    ward = rules.judge_ward(item["area"], store_name)
    if ward["status"] == "out":
        stats["out_of_area"] += 1
        return None
    if ward["status"] == "unknown":
        stats["ward_unknown"] += 1
    else:
        stats["candidates"] += 1
    return {
        "job_id": item.get("job_id", ""),  # 詳細取得の入口。stores.json には入れない
        "group_key": rules.store_key(store_name or item["store_field"], ward["station"]),
        "store_name": store_name,
        "location": ward["location"],
        "station": ward["station"],
        "ward_status": "浦和区" if ward["status"] == "urawa" else "区不明",
        "ward_basis": ward["basis"],
        "open_period": op["period"],
        "open_period_precision": op["precision"],
        "open_period_start": op["start"],
        "open_period_estimated": op["estimated"],
        "_phrase": op["phrase_key"],  # 名寄せ用の一時値。stores.json には書かない
        "site": item["site"],
        "list_url": list_url,
    }


# ---------------------------------------------------------------------------
# 蓄積(名寄せ)
# ---------------------------------------------------------------------------

def load_stores() -> list[dict]:
    try:
        return json.loads(STORES_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


def save_stores(stores: list[dict]) -> None:
    STORES_PATH.parent.mkdir(parents=True, exist_ok=True)
    stores.sort(key=lambda s: (s["ward_status"] != "浦和区", s["first_seen"], s["store_name"]))
    out = []
    for s in stores:
        rec = {k: v for k, v in s.items() if not k.startswith("_")}  # 一時値は書かない
        rec["publishable"], rec["publish_reason"] = rules.publish_judgement(rec["store_name"], rec["ward_status"])
        out.append(rec)
    STORES_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


def _address_of(location: str) -> str:
    return location.split("/")[0].strip()


_KNOWN_PRECISION = ("day", "ten_days", "month", "season")


def _towns_compatible(a: str, b: str) -> bool | None:
    """町名つきの勤務地どうしの比較。どちらかが区まで(町名なし)なら None(判断材料なし)。"""
    if a in ("", "さいたま市") or b in ("", "さいたま市"):
        return None
    return a.startswith(b) or b.startswith(a)  # 「区まで」と「町名つき」は同じ町とみなす


def find_existing(stores: list[dict], rec: dict) -> dict | None:
    key = rules.store_key(rec["store_name"], rec["station"], rec.get("_phrase", ""))
    for s in stores:
        if s["key"] == key:
            return s
    n = rules.normalize_name(rec["store_name"])
    if n:
        # 店名が同じで、片方だけ駅が空の場合は同一店舗とみなす
        for s in stores:
            if rules.normalize_name(s["store_name"]) == n:
                a, b = rules.normalize_station(s["station"]), rules.normalize_station(rec["station"])
                if not a or not b:
                    return s
    # 店名なしレコードと店名ありレコードの突き合わせ(誤統合を避けるため、迷ったら統合しない)。
    # 条件: どちらかが店名なし、最寄駅が同じ、町名が食い違わない、かつ次のいずれか。
    #   (a) 同じ実行内で、判定に使った句(メモリ上の一時値)が一致、または一方が他方の先頭(8文字以上)
    #   (b) 町名が両方わかっていて、正規化した開店時期(日/旬/月/季節)が一致
    ph = rec.get("_phrase", "")
    for s in stores:
        if s["store_name"] and n:
            continue  # 両方に店名があるときは店名で判断する(上で済み)
        sa, sb = rules.normalize_station(s["station"]), rules.normalize_station(rec["station"])
        if not (sa and sb and sa == sb):
            continue
        compat = _towns_compatible(_address_of(s["location"]), _address_of(rec["location"]))
        if compat is False:
            continue
        sp = s.get("_phrase", "")
        if len(ph) >= 8 and len(sp) >= 8 and (ph == sp or ph.startswith(sp) or sp.startswith(ph)):
            return s
        if (compat is True and rec["open_period_precision"] in _KNOWN_PRECISION
                and s.get("open_period") == rec["open_period"]):
            return s
    return None


def merge_record(stores: list[dict], rec: dict, run_date: str) -> bool:
    """名寄せして stores に反映する。新規発見なら True。"""
    ex = find_existing(stores, rec)
    if ex is None:
        stores.append({
            "key": rules.store_key(rec["store_name"], rec["station"], rec.get("_phrase", "")),
            "store_name": rec["store_name"],
            "location": rec["location"],
            "station": rec["station"],
            "ward_status": rec["ward_status"],
            "ward_basis": rec["ward_basis"],
            "open_period": rec["open_period"],
            "open_period_precision": rec["open_period_precision"],
            "open_period_start": rec["open_period_start"],
            "open_period_estimated": rec["open_period_estimated"],
            "sources": [{"site": rec["site"], "list_url": rec["list_url"], "last_seen": run_date}],
            "first_seen": run_date,
            "last_seen": run_date,
            "_phrase": rec.get("_phrase", ""),
        })
        return True
    ex["last_seen"] = run_date
    if not ex["store_name"] and rec["store_name"]:
        ex["store_name"] = rec["store_name"]
        ex["key"] = rules.store_key(rec["store_name"], rec["station"] or ex["station"], rec.get("_phrase", ""))
    if not ex["location"] and rec["location"]:
        ex["location"] = rec["location"]
    if not ex["station"] and rec["station"]:
        ex["station"] = rec["station"]
    if not ex.get("_phrase"):
        ex["_phrase"] = rec.get("_phrase", "")
    # 浦和区の根拠が取れたら区不明から昇格させる(逆はしない)
    if ex["ward_status"] == "区不明" and rec["ward_status"] == "浦和区":
        ex["ward_status"], ex["ward_basis"] = "浦和区", rec["ward_basis"]
    # 開店時期は、より精度の高いものがあれば置き換える
    if period.RANK[rec["open_period_precision"]] > period.RANK.get(ex.get("open_period_precision", "unknown"), 0):
        for k in ("open_period", "open_period_precision", "open_period_start", "open_period_estimated"):
            ex[k] = rec[k]
    for src in ex["sources"]:
        if src["site"] == rec["site"] and src["list_url"] == rec["list_url"]:
            src["last_seen"] = run_date
            break
    else:
        ex["sources"].append({"site": rec["site"], "list_url": rec["list_url"], "last_seen": run_date})
    return False


# ---------------------------------------------------------------------------
# サイトごとの巡回
# ---------------------------------------------------------------------------

def new_site_stats() -> dict:
    return {"status": "ok", "requests": 0, "pages": 0, "items_parsed": 0, "no_open_signal": 0, "snippet_only_undated": 0,
            "excluded": {}, "out_of_area": 0, "candidates": 0, "ward_unknown": 0, "note": ""}


def load_robots(fetcher: Fetcher, base_url: str, extra: list[str], stats: dict) -> list[str]:
    """robots.txt のDisallowと自前の禁止リストを合わせて返す。robots自体が取れなくても
    自前の禁止リストだけで続行する。"""
    patterns = list(extra)
    try:
        patterns += parse_robots_disallow(fetcher.get(base_url + "/robots.txt"))
    except Exception as e:  # noqa: BLE001
        stats["note"] += f"robots.txt取得失敗({type(e).__name__}) "
    stats["requests"] += 1
    return patterns


def save_snapshot(name: str, url: str, html: str) -> None:
    """環境変数 URAWA_SAVE_HTML_DIR があるときだけ、取得した一覧HTMLをテスト用フィクスチャとして
    保存する(求人本文の転載になるのでリポジトリ外には出さない。.gitignore で除外済み)。"""
    d = os.environ.get("URAWA_SAVE_HTML_DIR")
    if not d:
        return
    label = snapshot_label(url)
    out = Path(d) / name
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{label}.html").write_text(html, encoding="utf-8")


def crawl_site(name: str, fetcher: Fetcher, urls_iter, parse, forbidden: list[str],
               collected: list[dict], stats: dict) -> None:
    """urls_iter は (url, 次ページ判定コールバック付きの) ジェネレータ。"""
    for url, state in urls_iter:
        try:
            html = fetcher.get(url, forbidden)
        except BlockedError as e:
            stats["status"] = "blocked"
            stats["note"] += f"{e} / 回避せず中止 "
            print(f"[blocked] {name}: {e}", file=sys.stderr)
            return
        except ForbiddenPathError as e:
            stats["note"] += f"禁止パスのためスキップ: {e} "
            continue
        except BudgetExceededError:
            stats["status"] = "budget_exceeded"
            return
        except Exception as e:  # noqa: BLE001
            stats["status"] = "error"
            stats["note"] += f"{type(e).__name__}: {e} "
            print(f"[error] {name} {url}: {e}", file=sys.stderr)
            return
        stats["requests"] += 1
        stats["pages"] += 1
        save_snapshot(name, url, html)
        items = parse(html)
        state["last_count"] = len(items)
        state["has_next"] = parsers.kyujinbox_has_next(html) if name == "kyujinbox" else None
        if not items:
            stats["note"] += f"0件のページ: {url} "
        for it in items:
            rec = classify_item(it, url, stats)
            if rec:
                collected.append(rec)


# ---------------------------------------------------------------------------
# バイトルの求人詳細(候補になった店だけ・1店舗につき1件・上限あり)
# ---------------------------------------------------------------------------

def apply_detail(rec: dict, d: dict) -> dict | None:
    """詳細から取れた店名・住所・開店時期で rec を更新した新しい dict を返す。
    詳細の住所で区判定をやり直した結果が「対象外」なら None。"""
    new = dict(rec)
    detail_name = rules.extract_store_name(d.get("store_title", ""))
    if detail_name and (not rec["store_name"] or rules.COMPANY_RE.search(rec["store_name"])):
        new["store_name"] = detail_name
    area = ""
    if d.get("locality"):
        area = f"埼玉県 {d['locality']}"
        if d.get("street"):
            area += f" {d['street']}"
        st = d.get("station") or rec["station"]
        if st:
            area += f" / {st}駅"
        ward = rules.judge_ward(area, new["store_name"])
        if ward["status"] == "out":
            return None
        new["location"], new["station"] = ward["location"], ward["station"]
        new["ward_status"] = "浦和区" if ward["status"] == "urawa" else "区不明"
        new["ward_basis"] = ward["basis"] + "(詳細ページの住所)"
    # 開店時期: 詳細(見出しと開店語を含む行)から正規化し、一覧より精度が高ければ置き換える。
    # 開店済みと読めた場合は一覧の値を変えない(除外は一覧の段階で済んでいる)
    det_op = rules.open_period_from([d.get("open_text", "")], run_today())
    if period.RANK[det_op["precision"]] > period.RANK[rec["open_period_precision"]] and not det_op["past"]:
        new["open_period"], new["open_period_precision"] = det_op["period"], det_op["precision"]
        new["open_period_start"], new["open_period_estimated"] = det_op["start"], det_op["estimated"]
    return new


def fetch_baitoru_details(fetcher: Fetcher, recs: list[dict], forbidden: list[str], stats: dict) -> list[dict]:
    """候補(浦和区/区不明)の求人を店舗グループごとに1件だけ詳細取得し、同じグループの
    全レコードに反映して返す。403/429が出たらその時点で止める(回避しない)。"""
    limit = CONFIG.get("max_detail_fetches", 15)
    det = stats["detail"] = {"groups": 0, "attempted": 0, "ok": 0, "failed": 0, "name_filled": 0,
                             "period_filled": 0, "dropped_out_of_area": 0, "blocked": False,
                             "skipped_over_limit": 0}
    cache: dict[str, dict | None] = {}
    out: list[dict] = []
    stop = False
    for rec in recs:
        g = rec["group_key"]
        if g not in cache:
            det["groups"] += 1
            d = None
            if stop or not rec.get("job_id"):
                pass
            elif det["attempted"] >= limit:
                det["skipped_over_limit"] += 1
            else:
                det["attempted"] += 1
                url = f"{CONFIG['baitoru']['base_url']}/jobdetail/{rec['job_id']}/"
                try:
                    html = fetcher.get(url, forbidden)
                    d = parsers.parse_baitoru_detail(html)
                    det["ok"] += 1
                except BlockedError as e:
                    det["blocked"] = True
                    stop = True
                    stats["note"] += f"詳細取得で{e} / 詳細取得を中止 "
                    print(f"[blocked] baitoru detail: {e}", file=sys.stderr)
                except (ForbiddenPathError, BudgetExceededError) as e:
                    det["failed"] += 1
                    stop = True
                    stats["note"] += f"詳細取得スキップ: {type(e).__name__} "
                except Exception as e:  # noqa: BLE001
                    det["failed"] += 1
                    stats["note"] += f"詳細失敗 {type(e).__name__}: {e} "
            cache[g] = d
        d = cache[g]
        if d is None:
            out.append(rec)
            continue
        new = apply_detail(rec, d)
        if new is None:
            det["dropped_out_of_area"] += 1
            continue
        if new["store_name"] and not rec["store_name"]:
            det["name_filled"] += 1
        if new["open_period"] != rec["open_period"]:
            det["period_filled"] += 1
        out.append(new)
    return out


def baitoru_pages(max_pages: int):
    state: dict = {}
    for url in baitoru_urls(max_pages):
        yield url, state
        if state.get("last_count") == 0:
            return


def kyujinbox_pages(keyword: str, max_pages: int):
    state: dict = {}
    for page in range(1, max_pages + 1):
        yield kyujinbox_url(keyword, page), state
        if not state.get("last_count") or not state.get("has_next"):
            return


def publish_false_counts(stores: list[dict]) -> dict:
    out: dict = {}
    for s in stores:
        ok, why = rules.publish_judgement(s["store_name"], s["ward_status"])
        if not ok:
            out[why] = out.get(why, 0) + 1
    return out


def main(only: str | None = None) -> int:
    offline_dir = os.environ.get("URAWA_OFFLINE_HTML_DIR")
    if offline_dir:  # 保存済みHTMLを読むだけ。ネットワークには出ない
        fetcher = OfflineFetcher(offline_dir)
    else:
        fetcher = Fetcher(CONFIG["user_agent"], CONFIG["request_interval_seconds"], CONFIG["max_requests_per_run"])
    max_pages = CONFIG["max_pages_per_list"]
    run_date = today()
    stores = load_stores()
    summary = {"run_at": now_iso(), "mode": "offline" if offline_dir else "live", "new_found": 0, "sites": {}}

    if only in (None, "baitoru"):
        st = summary["sites"]["baitoru"] = new_site_stats()
        cfg = CONFIG["baitoru"]
        forbidden = load_robots(fetcher, cfg["base_url"], cfg["forbidden_prefixes"], st)
        recs: list[dict] = []
        crawl_site("baitoru", fetcher, baitoru_pages(max_pages), parsers.parse_baitoru_list,
                   forbidden, recs, st)
        # 詳細ページは robots.txt が許す場合だけ(forbidden に /jobdetail/ が入っていれば取らない)
        recs = fetch_baitoru_details(fetcher, recs, forbidden, st)
        st["requests"] += st["detail"]["attempted"]
        for rec in recs:
            if merge_record(stores, rec, run_date):
                summary["new_found"] += 1

    if only in (None, "kyujinbox"):
        cfg = CONFIG["kyujinbox"]
        agg = summary["sites"]["kyujinbox"] = new_site_stats()
        forbidden = load_robots(fetcher, cfg["base_url"], cfg["forbidden_prefixes"], agg)
        by_kw = {}
        plan = [(kw, max_pages) for kw in cfg["keywords"]] + \
               [(kw, cfg.get("extra_max_pages", 2)) for kw in cfg.get("extra_keywords", [])]
        for kw, kw_pages in plan:
            st = by_kw[kw] = new_site_stats()
            recs = []
            crawl_site("kyujinbox", fetcher, kyujinbox_pages(kw, kw_pages), parsers.parse_kyujinbox_list,
                       forbidden, recs, st)
            for rec in recs:
                if merge_record(stores, rec, run_date):
                    summary["new_found"] += 1
            if st["status"] == "blocked":
                break  # 403等になったらこのサイトは止める
        agg["by_keyword"] = by_kw
        for st in by_kw.values():
            agg["requests"] += st["requests"]
            agg["pages"] += st["pages"]
            for k in ("items_parsed", "no_open_signal", "snippet_only_undated", "out_of_area", "candidates", "ward_unknown"):
                agg[k] += st[k]
            for r, n in st["excluded"].items():
                agg["excluded"][r] = agg["excluded"].get(r, 0) + n
            agg["note"] += st["note"]
            if st["status"] != "ok":
                agg["status"] = st["status"]

    sites = summary["sites"].values()
    # 全サイトで一覧が1件も取れなかった(403/429・構造変更・通信失敗など)場合は、stores.json を
    # 書き換えずに非ゼロで終了する(ジョブを失敗させ、data/ と docs/ を壊さない)。
    # 一部のサイトだけ成功したときは、そのぶんを保存して0で終わる
    all_failed = sum(s["items_parsed"] for s in sites) == 0
    if not all_failed:
        save_stores(stores)
    excluded_total: dict = {}
    for s in sites:
        for r, n in s["excluded"].items():
            excluded_total[r] = excluded_total.get(r, 0) + n
    summary.update({
        "total_requests": fetcher.count,
        "items_parsed": sum(s["items_parsed"] for s in sites),
        "no_open_signal": sum(s["no_open_signal"] for s in sites),
        "snippet_only_undated_dropped": sum(s["snippet_only_undated"] for s in sites),
        "candidate_hits": sum(s["candidates"] for s in sites),
        "ward_unknown_hits": sum(s["ward_unknown"] for s in sites),
        "excluded_total": sum(excluded_total.values()),
        "excluded_by_reason": excluded_total,
        "out_of_area": sum(s["out_of_area"] for s in sites),
        "stores_total": len(stores),
        "stores_urawa": sum(1 for s in stores if s["ward_status"] == "浦和区"),
        "stores_ward_unknown": sum(1 for s in stores if s["ward_status"] == "区不明"),
        "publishable_true": sum(1 for s in stores if rules.publish_judgement(s["store_name"], s["ward_status"])[0]),
        "publishable_false_by_reason": publish_false_counts(stores),
    })
    summary["all_failed"] = all_failed
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "sites"}, ensure_ascii=False))
    if all_failed:
        print("[error] 全サイトで一覧を取得できませんでした。stores.json は更新していません", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
