#!/usr/bin/env python3
"""fixtures/ の一覧ページを相手にしたパーサ・判定のテスト。本番へは一切アクセスしない。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import crawl  # noqa: E402
import parsers  # noqa: E402

FIX = ROOT / "tests" / "fixtures"

# 一覧ページのスナップショットはリポジトリに入れていない(求人本文の転載を避けるため)。
# 手元に無い環境では、このモジュールのテストは丸ごとスキップする。
if not (FIX.is_dir() and any(FIX.iterdir())):
    raise unittest.SkipTest("一覧ページのスナップショット(tests/fixtures/)が無い。リポジトリには入れていない")


def read(path: str) -> str:
    return (FIX / path).read_text(encoding="utf-8")


def classify_all(items, stats=None):
    stats = stats or crawl.new_site_stats()
    recs = [crawl.classify_item(it, "test", stats) for it in items]
    return [r for r in recs if r], stats


class TestBaitoru(unittest.TestCase):
    def test_parse_list_excludes_recommend_block(self):
        items = parsers.parse_baitoru_list(read("baitoru/list_page1.html"))
        self.assertEqual(len(items), 30)  # 下部の「おすすめ」枠(recom_)は含めない
        self.assertTrue(all(i["site"] == "baitoru" for i in items))
        pr = [i for i in items if i["is_pr"]]
        self.assertTrue(pr)
        self.assertTrue(any("浦和区" not in i["area"] for i in pr))  # PR枠は他の区・市の求人が混じる

    def test_pr_other_area_is_dropped_by_ward_judgement(self):
        items = parsers.parse_baitoru_list(read("baitoru/list_page1.html"))
        stats = crawl.new_site_stats()
        recs, _ = classify_all([i for i in items if i["is_pr"]], stats)
        self.assertEqual(recs, [])

    def test_kuraz_sushi_detected_somewhere_in_three_pages(self):
        recs = []
        for n in (1, 2, 3):
            items = parsers.parse_baitoru_list(read(f"baitoru/list_page{n}.html"))
            recs += classify_all(items)[0]
        names = {r["store_name"]: r for r in recs}
        self.assertIn("無添くら寿司 浦和コルソ店", names)
        r = names["無添くら寿司 浦和コルソ店"]
        self.assertEqual(r["ward_status"], "浦和区")
        self.assertEqual(r["open_period_precision"], "unknown")  # 「新店オープン」だけで時期は書かれていない
        self.assertEqual(r["open_period"], "時期不明（開店予定の募集あり）")

    def test_yono_is_not_urawa(self):
        recs = []
        for n in (1, 2, 3):
            recs += classify_all(parsers.parse_baitoru_list(read(f"baitoru/list_page{n}.html")))[0]
        yono = [r for r in recs if "与野日和" in r["store_name"]]
        self.assertEqual(len(yono), 1)
        self.assertEqual(yono[0]["ward_status"], "区不明")


class TestBaitoruDetail(unittest.TestCase):
    def test_parse_kura(self):
        d = parsers.parse_baitoru_detail(read("baitoru_detail/kura.html"))
        self.assertEqual(d["store_title"], "無添くら寿司 浦和コルソ店")
        self.assertEqual(d["locality"], "さいたま市浦和区")
        self.assertEqual(d["street"], "高砂")
        self.assertEqual(d["station"], "浦和")
        self.assertEqual(d["date_posted"], "2026-10-12")

    def test_parse_yono(self):
        d = parsers.parse_baitoru_detail(read("baitoru_detail/yono.html"))
        self.assertEqual(d["station"], "与野")
        self.assertEqual(d["date_posted"], "2026-10-21")
        self.assertNotIn("giyou", d)  # 本文・給与のキーは持たない
        self.assertEqual(set(d), {"store_title", "locality", "street", "station", "date_posted", "open_text"})

    def test_apply_detail_fills_name_and_keeps_yono_unknown(self):
        base = {"job_id": "1", "group_key": "g", "store_name": "", "location": "さいたま市浦和区 / 浦和駅", "station": "浦和",
                "ward_status": "浦和区", "ward_basis": "x", "open_period": "時期不明（開店予定の募集あり）", "open_period_precision": "unknown",
                "open_period_start": None, "open_period_estimated": False, "site": "baitoru", "list_url": "u"}
        new = crawl.apply_detail(base, parsers.parse_baitoru_detail(read("baitoru_detail/kura.html")))
        self.assertEqual(new["store_name"], "無添くら寿司 浦和コルソ店")
        self.assertNotIn("posted_date", new)  # 掲載開始日は保存しない
        self.assertEqual(new["ward_status"], "浦和区")
        y = crawl.apply_detail(dict(base, station="与野"), parsers.parse_baitoru_detail(read("baitoru_detail/yono.html")))
        self.assertEqual(y["store_name"], "手揉み麺 与野日和")
        self.assertEqual(y["ward_status"], "区不明")

    def test_one_detail_per_store_and_limit_and_block(self):
        kura = read("baitoru_detail/kura.html")
        recs = [{"job_id": str(i), "group_key": "same", "store_name": "", "location": "", "station": "浦和", "ward_status": "浦和区",
                 "ward_basis": "x", "open_period": "時期不明（開店予定の募集あり）", "open_period_precision": "unknown",
                 "open_period_start": None, "open_period_estimated": False, "site": "baitoru", "list_url": "u"} for i in range(4)]
        recs += [dict(recs[0], job_id=str(10 + i), group_key=f"g{i}") for i in range(3)]

        class Fake:
            def __init__(self, fail_at=None):
                self.urls, self.fail_at = [], fail_at

            def get(self, url, forbidden=None):
                self.urls.append(url)
                if self.fail_at is not None and len(self.urls) > self.fail_at:
                    raise crawl.BlockedError("HTTP 403")
                return kura

        f, st = Fake(), crawl.new_site_stats()
        out = crawl.fetch_baitoru_details(f, recs, [], st)
        self.assertEqual(len(f.urls), 4)  # 4件同一店舗→1件 + 別店舗3件
        self.assertEqual(len(out), 7)
        self.assertEqual(st["detail"]["name_filled"], 7)
        # 上限
        orig = crawl.CONFIG["max_detail_fetches"]
        crawl.CONFIG["max_detail_fetches"] = 2
        try:
            f2, st2 = Fake(), crawl.new_site_stats()
            crawl.fetch_baitoru_details(f2, recs, [], st2)
            self.assertEqual(len(f2.urls), 2)
            self.assertEqual(st2["detail"]["skipped_over_limit"], 2)
        finally:
            crawl.CONFIG["max_detail_fetches"] = orig
        # 403で止まる
        f3, st3 = Fake(fail_at=1), crawl.new_site_stats()
        out3 = crawl.fetch_baitoru_details(f3, recs, [], st3)
        self.assertTrue(st3["detail"]["blocked"])
        self.assertEqual(len(f3.urls), 2)  # 2件目で403→以降は取らない
        self.assertEqual(len(out3), 7)  # 取れなかった分も一覧段階のまま残る

    def test_detail_path_respects_robots(self):
        from fetch import is_path_forbidden
        self.assertFalse(is_path_forbidden("/jobdetail/165389277/", crawl.CONFIG["baitoru"]["forbidden_prefixes"]))
        self.assertTrue(is_path_forbidden("/jobdetail/165389277/", ["/jobdetail/"]))


class TestKyujinbox(unittest.TestCase):
    def test_parse_list(self):
        items = parsers.parse_kyujinbox_list(read("kyujinbox/opening_staff_p1.html"))
        self.assertEqual(len(items), 24)
        self.assertTrue(all(i["area"] for i in items))

    def _all_records(self):
        recs = []
        for kw in ("opening_staff", "opening", "shinki_open"):
            for n in (1, 2, 3):
                recs += classify_all(parsers.parse_kyujinbox_list(read(f"kyujinbox/{kw}_p{n}.html")))[0]
        return recs

    def test_known_new_stores_detected(self):
        names = {r["store_name"] for r in self._all_records()}
        self.assertIn("福恩麻辣湯 北浦和駅前店", names)
        self.assertIn("北浦和PEACE保育園", names)

    def test_peace_hoikuen_period_and_ward(self):
        r = next(r for r in self._all_records() if r["store_name"] == "北浦和PEACE保育園")
        self.assertEqual(r["open_period"], "2027年4月")
        self.assertEqual(r["open_period_precision"], "month")
        self.assertEqual(r["open_period_start"], "2027-04-01")
        self.assertEqual(r["ward_status"], "浦和区")

    def test_generic_opening_staff_is_not_candidate(self):
        # 「オープニングスタッフ」だけの定型文(コールセンター等)は新店にしない
        names = {r["store_name"] for r in self._all_records()}
        self.assertNotIn("株式会社日本パーソナルビジネス", names)

    def test_no_stored_text_beyond_allowed_fields(self):
        allowed = {"store_name", "location", "station", "ward_status", "ward_basis", "site", "list_url",
                   "open_period", "open_period_precision", "open_period_start", "open_period_estimated",
                   "job_id", "group_key", "_phrase"}  # job_id/group_key/_phrase は一時値で、stores.json には入れない
        self.assertNotIn("opening_excerpt", allowed)
        for r in self._all_records():
            self.assertEqual(set(r), allowed)


class TestPageNext(unittest.TestCase):
    def test_has_next(self):
        self.assertTrue(parsers.kyujinbox_has_next(read("kyujinbox/opening_staff_p1.html")))


if __name__ == "__main__":
    unittest.main()
