#!/usr/bin/env python3
"""判定ルール・名寄せのユニットテスト(HTML不要。fixtures が無くても動く)。"""

import datetime
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import crawl  # noqa: E402
import rules  # noqa: E402
from fetch import is_path_forbidden, parse_robots_disallow  # noqa: E402


def item(title="", store="", snippet="", area="埼玉県 さいたま市 浦和区 / 浦和駅"):
    return {"site": "kyujinbox", "title": title, "store_field": store, "snippet": snippet, "area": area, "is_pr": False}


class TestWard(unittest.TestCase):
    def test_urawa_by_address(self):
        j = rules.judge_ward("埼玉県 さいたま市 浦和区 高砂 / 浦和駅 徒歩1分")
        self.assertEqual(j["status"], "urawa")
        self.assertEqual(j["location"], "さいたま市浦和区高砂 / 浦和駅")

    def test_urawa_by_station_only(self):
        self.assertEqual(rules.judge_ward("埼玉県 さいたま市 北浦和駅")["status"], "urawa")
        self.assertEqual(rules.judge_ward("さいたま市 浦和駅 徒歩5分")["status"], "urawa")

    def test_not_urawa_stations(self):
        for st in ("武蔵浦和駅", "中浦和駅", "東浦和駅", "浦和美園駅", "与野駅", "さいたま新都心駅", "大宮駅"):
            self.assertEqual(rules.judge_ward(f"埼玉県 さいたま市 {st}")["status"], "out", st)

    def test_address_urawa_but_yono_station_is_unknown(self):
        j = rules.judge_ward("埼玉県 さいたま市浦和区 / 与野駅 (徒歩1分) ほか")
        self.assertEqual(j["status"], "unknown")

    def test_name_with_other_place_is_unknown(self):
        j = rules.judge_ward("埼玉県 さいたま市浦和区", "ケンタッキー与野東口店")
        self.assertEqual(j["status"], "unknown")

    def test_other_ward_or_prefecture_is_out(self):
        self.assertEqual(rules.judge_ward("埼玉県 さいたま市 緑区 中尾 / 浦和駅 バス20分")["status"], "out")
        self.assertEqual(rules.judge_ward("東京都 千代田区 / 神田駅 (徒歩3分)")["status"], "out")
        self.assertEqual(rules.judge_ward("埼玉県 戸田市 / 戸田公園駅 (バス15分)")["status"], "out")

    def test_unknown_when_nothing_to_go_on(self):
        self.assertEqual(rules.judge_ward("埼玉県 さいたま市")["status"], "unknown")


class TestOpenSignal(unittest.TestCase):
    def test_keywords(self):
        for t in ("12月上旬OPEN", "新規オープン", "NEW OPEN", "ＮＥＷ　ＯＰＥＮ", "新店", "4月に開園", "開校予定", "新規開院"):
            self.assertTrue(rules.has_open_signal(t), t)

    def test_not_signal(self):
        for t in ("オープニングスタッフ募集", "オープンキッチンあり", "新規事業の立ち上げ", "新規フリー客多数",
                  "オープン3年以内", "お昼15時~OPEN", "16OPEN人前が苦手", "2026年もNEW OPEN", "今年もNEW OPEN",
                  "店長候補募集", "今後も新規出店を予定しています", "今後も 新たなクリニックの 開院を予定", "今後は菊名での開院を予定", "今後も新たなクリニックの開院を予定するなど", "オープニングメンバー募集", "リニューアルオープン", "新店舗続々", "年間20教室を新規オープン"):
            self.assertFalse(rules.has_open_signal(t), t)

    def test_excerpt_short_and_dated(self):
        e = rules.extract_excerpt("ホールスタッフ／「2026年11月OPEN」人気麻辣湯店で大量募集", "", "")
        self.assertEqual(e, "2026年11月OPEN")
        long = rules.extract_excerpt("あ" * 40 + "オープン" + "い" * 40)
        self.assertLessEqual(len(long), 30)
        self.assertIn("オープン", long)

    def test_excerpt_strips_backslash_decoration(self):
        self.assertEqual(rules.extract_excerpt("\\11月24日OPEN"), "11月24日OPEN")

    def test_excerpt_collapses_highlight_spaces(self):
        self.assertEqual(rules.extract_excerpt("", "", "2027年10月 オープン 予定の施設"), "2027年10月オープン予定の施設")


class TestOtherArea(unittest.TestCase):
    def test_other_city_or_ward_in_text(self):
        for t in ("仙台市名坂2026年8月17日OPEN", "さいたま市緑区に新規オープン予定", "川口市に新規オープン", "神奈川県横浜市に開院"):
            self.assertTrue(rules.other_area_in_text(t), t)

    def test_urawa_and_plain_text_not_flagged(self):
        for t in ("さいたま市浦和区に新規オープン", "埼玉県さいたま市浦和区北浦和周辺にオープン予定", "2026年11月OPEN", "市場に新規オープン"):
            self.assertFalse(rules.other_area_in_text(t), t)

    def test_classify_drops_sendai(self):
        st = crawl.new_site_stats()
        r = crawl.classify_item(item(title="店長候補", snippet="仙台市名坂2026年8月17日OPEN仙台市泉区市名坂中道1"), "u", st)
        self.assertIsNone(r)


class TestExclusion(unittest.TestCase):
    def reason(self, title, excerpt="オープン"):
        return rules.exclusion_reason(title, "", excerpt)

    def test_reasons(self):
        self.assertEqual(self.reason("交通誘導警備 新規オープン"), "security_guidance")
        self.assertEqual(self.reason("試験監督スタッフ"), "exam")
        self.assertEqual(self.reason("催事の物産展 オープン"), "event")
        self.assertEqual(self.reason("既存店 増員募集"), "staff_increase")
        self.assertEqual(self.reason("短期バイト オープン"), "short_term")
        self.assertEqual(self.reason("＜11/20～12/6＞ オープン"), "date_range_short")

    def test_daily_pay_only_vs_dated_open(self):
        self.assertEqual(self.reason("日払いOK 新規オープン", "新規オープン"), "daily_pay_only")
        self.assertIsNone(self.reason("日払いOK 12月OPEN", "12月OPEN"))

    def test_normal_store_not_excluded(self):
        self.assertIsNone(self.reason("ホールスタッフ／「2026年11月OPEN」"))


class TestStoreName(unittest.TestCase):
    def test_company_names_are_empty(self):
        self.assertEqual(rules.extract_store_name("株式会社アクトプラス　運営事業部"), "")
        self.assertEqual(rules.extract_store_name("医療法人社団ひまわり会"), "")

    def test_store_names_kept(self):
        self.assertEqual(rules.extract_store_name("無添くら寿司　浦和コルソ店"), "無添くら寿司 浦和コルソ店")

    def test_quoted_store_in_promo_field(self):
        self.assertEqual(rules.extract_store_name("与野で新オープン！「手揉み麺　与野日和」で新スタッフを募集します！"), "手揉み麺 与野日和")


class TestNormalize(unittest.TestCase):
    def test_same_store_same_key(self):
        a = rules.store_key("無添くら寿司　浦和コルソ店", "浦和駅")
        b = rules.store_key("無添くら寿司 浦和コルソ", "浦和")
        c = rules.store_key("無添くら寿司 浦和コルソ店", "浦和駅 (徒歩1分)".split()[0])
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_fullwidth(self):
        self.assertEqual(rules.normalize_name("ＬａＢｏ　浦和店"), rules.normalize_name("labo浦和"))

    def test_different_station_different_key(self):
        self.assertNotEqual(rules.store_key("ABC店", "浦和駅"), rules.store_key("ABC店", "北浦和駅"))


class TestMerge(unittest.TestCase):
    def rec(self, site, url, name="無添くら寿司 浦和コルソ店", station="浦和", status="浦和区", period="2026年12月上旬"):
        return {"store_name": name, "location": "さいたま市浦和区", "station": station, "ward_status": status,
                "ward_basis": "x", "open_period": period, "open_period_precision": "ten_days" if "旬" in period else "unknown",
                "open_period_start": None, "open_period_estimated": False, "site": site, "list_url": url}

    def test_merge_keeps_first_seen_and_appends_sources(self):
        stores = []
        self.assertTrue(crawl.merge_record(stores, self.rec("baitoru", "u1"), "2026-10-01"))
        self.assertFalse(crawl.merge_record(stores, self.rec("kyujinbox", "u2", name="無添くら寿司　浦和コルソ"), "2026-10-24"))
        self.assertEqual(len(stores), 1)
        s = stores[0]
        self.assertEqual(s["first_seen"], "2026-10-01")
        self.assertEqual(s["last_seen"], "2026-10-24")
        self.assertEqual({x["site"] for x in s["sources"]}, {"baitoru", "kyujinbox"})
        crawl.merge_record(stores, self.rec("baitoru", "u1"), "2026-10-25")
        self.assertEqual(len(stores[0]["sources"]), 2)
        self.assertEqual(stores[0]["sources"][0]["last_seen"], "2026-10-25")

    def test_unknown_promoted_to_urawa(self):
        stores = []
        crawl.merge_record(stores, self.rec("baitoru", "u1", status="区不明"), "2026-10-01")
        crawl.merge_record(stores, self.rec("kyujinbox", "u2", status="浦和区"), "2026-10-02")
        self.assertEqual(stores[0]["ward_status"], "浦和区")



class TestNamelessMerge(unittest.TestCase):
    def rec(self, name, station, period, loc="さいたま市浦和区高砂 / 浦和駅", phrase=""):
        prec = "ten_days" if "旬" in period else ("month" if "月" in period else "unknown")
        return {"store_name": name, "location": loc, "station": station, "ward_status": "浦和区", "ward_basis": "x",
                "open_period": period, "open_period_precision": prec, "open_period_start": None,
                "open_period_estimated": False, "site": "kyujinbox", "list_url": "u", "_phrase": phrase}

    def test_same_run_phrase_prefix_merges_both_orders(self):
        long = rules.normalize_name("個別指導塾 2026年12月オープン予定スタッフ")
        short = rules.normalize_name("個別指導塾 2026年12月オープン")
        for first, second in (("", "塾A 浦和校"), ("塾A 浦和校", "")):
            stores = []
            crawl.merge_record(stores, self.rec(first, "浦和", "時期不明（開店予定の募集あり）", phrase=long), "2026-10-24")
            crawl.merge_record(stores, self.rec(second, "浦和", "時期不明（開店予定の募集あり）", phrase=short), "2026-10-24")
            self.assertEqual(len(stores), 1)
            self.assertEqual(stores[0]["store_name"], "塾A 浦和校")

    def test_period_station_town_match_merges_without_phrase(self):
        """句を持たない(前回実行から読み込んだ)レコードでも、駅+町名+開店時期が一致すれば寄せる"""
        stores = []
        crawl.merge_record(stores, self.rec("", "浦和", "2027年4月"), "2026-10-24")
        crawl.merge_record(stores, self.rec("塾B", "浦和", "2027年4月"), "2026-10-24")
        self.assertEqual(len(stores), 1)

    def test_period_match_needs_known_town(self):
        stores = []
        crawl.merge_record(stores, self.rec("", "浦和", "2027年4月", loc="さいたま市 / 浦和駅"), "2026-10-24")
        crawl.merge_record(stores, self.rec("塾C", "浦和", "2027年4月", loc="さいたま市 / 浦和駅"), "2026-10-24")
        self.assertEqual(len(stores), 2)  # 町名が無ければ時期だけでは統合しない

    def test_unknown_period_never_merges_by_period(self):
        stores = []
        crawl.merge_record(stores, self.rec("", "浦和", "時期不明（開店予定の募集あり）"), "2026-10-24")
        crawl.merge_record(stores, self.rec("塾D", "浦和", "時期不明（開店予定の募集あり）"), "2026-10-24")
        self.assertEqual(len(stores), 2)

    def test_different_station_or_town_not_merged(self):
        stores = []
        crawl.merge_record(stores, self.rec("", "浦和", "2027年4月"), "2026-10-24")
        crawl.merge_record(stores, self.rec("塾E", "北浦和", "2027年4月", loc="さいたま市浦和区高砂 / 北浦和駅"), "2026-10-24")
        crawl.merge_record(stores, self.rec("塾F", "浦和", "2027年4月", loc="さいたま市浦和区仲町 / 浦和駅"), "2026-10-24")
        self.assertEqual(len(stores), 3)

    def test_ward_only_vs_town_address_phrase_merge(self):
        ph = rules.normalize_name("個別指導塾 2026年12月オープン予定スタッフ")
        stores = []
        crawl.merge_record(stores, self.rec("塾G", "浦和", "x", loc="さいたま市浦和区 / 浦和駅", phrase=ph), "2026-10-24")
        crawl.merge_record(stores, self.rec("", "浦和", "x", loc="さいたま市浦和区高砂 / 浦和駅", phrase=ph), "2026-10-24")
        self.assertEqual(len(stores), 1)
        stores = []
        crawl.merge_record(stores, self.rec("", "浦和", "x", loc="さいたま市浦和区高砂 / 浦和駅", phrase=ph), "2026-10-24")
        crawl.merge_record(stores, self.rec("塾H", "浦和", "x", loc="さいたま市浦和区仲町 / 浦和駅", phrase=ph), "2026-10-24")
        self.assertEqual(len(stores), 2)

    def test_two_named_stores_never_merged(self):
        ph = rules.normalize_name("2026年12月オープン予定の施設です")
        stores = []
        crawl.merge_record(stores, self.rec("塾I", "浦和", "2027年4月", phrase=ph), "2026-10-24")
        crawl.merge_record(stores, self.rec("塾J", "浦和", "2027年4月", phrase=ph), "2026-10-24")
        self.assertEqual(len(stores), 2)

    def test_keys_never_contain_phrase_text(self):
        stores = []
        crawl.merge_record(stores, self.rec("", "浦和", "2027年4月", phrase=rules.normalize_name("個別指導塾 2026年12月オープン")), "2026-10-24")
        self.assertRegex(stores[0]["key"], r"^\(店名不明\)\|浦和\|[0-9a-f]{8}$")


class TestSavedFields(unittest.TestCase):
    ALLOWED = {"key", "store_name", "location", "station", "ward_status", "ward_basis", "open_period",
               "open_period_precision", "open_period_start", "open_period_estimated", "sources", "first_seen",
               "last_seen", "publishable", "publish_reason"}

    def test_saved_json_has_only_allowed_fields_and_no_job_text(self):
        import json
        import tempfile
        secret = "求人本文っぽい文言ABC"
        rec = {"store_name": "塾K", "location": "さいたま市浦和区高砂 / 浦和駅", "station": "浦和", "ward_status": "浦和区",
               "ward_basis": "住所に「さいたま市浦和区」", "open_period": "2027年4月", "open_period_precision": "month",
               "open_period_start": "2027-04-01", "open_period_estimated": False, "site": "kyujinbox", "list_url": "u",
               "_phrase": secret, "job_id": "1", "group_key": "g"}
        stores = []
        crawl.merge_record(stores, rec, "2026-10-24")
        orig = crawl.STORES_PATH
        with tempfile.TemporaryDirectory() as tmp:
            crawl.STORES_PATH = Path(tmp) / "stores.json"
            try:
                crawl.save_stores(stores)
                text = crawl.STORES_PATH.read_text(encoding="utf-8")
            finally:
                crawl.STORES_PATH = orig
        self.assertNotIn(secret, text)
        self.assertNotIn("excerpt", text)
        data = json.loads(text)
        self.assertEqual(set(data[0]), self.ALLOWED)
        self.assertEqual(set(data[0]["sources"][0]), {"site", "list_url", "last_seen"})


class TestPublishable(unittest.TestCase):
    def test_flags(self):
        j = rules.publish_judgement
        self.assertEqual(j("無添くら寿司 浦和コルソ店", "浦和区"), (True, "ok"))
        self.assertEqual(j("", "浦和区"), (False, "no_store_name"))
        self.assertEqual(j("福恩麻辣湯 北浦和駅前店", "区不明"), (False, "ward_unknown"))
        self.assertEqual(j("就労継続支援B型", "浦和区"), (False, "name_is_category"))
        self.assertEqual(j("CME医師", "浦和区"), (False, "name_is_job_title"))
        self.assertEqual(j("ちゅ~る。ーwarabiー", "浦和区"), (False, "name_is_promo"))
        self.assertEqual(j("tocca hair&treatment by ...", "浦和区"), (False, "name_truncated"))
        self.assertEqual(j("匠 北浦和駅前整体院", "浦和区"), (True, "ok"))
        self.assertEqual(j("浦和カルエ眼科", "浦和区"), (True, "ok"))
        self.assertEqual(j("あかちゃんとこどものクリニック 浦和カルエ", "浦和区"), (True, "ok"))


class TestClassify(unittest.TestCase):
    def test_excluded_counted(self):
        st = crawl.new_site_stats()
        r = crawl.classify_item(item(title="【短期】新規オープン記念イベントスタッフ"), "u", st)
        self.assertIsNone(r)
        self.assertEqual(sum(st["excluded"].values()), 1)

    def test_snippet_only_undated_dropped(self):
        st = crawl.new_site_stats()
        r = crawl.classify_item(item(title="介護職", store="ひまわり", snippet="新規 オープン 施設"), "u", st)
        self.assertIsNone(r)
        self.assertEqual(st["snippet_only_undated"], 1)

    def test_snippet_only_undated_kept_when_shinten(self):
        st = crawl.new_site_stats()
        r = crawl.classify_item(item(title="店舗スタッフ", store="洋麺屋五右衛門 浦和駅前店", snippet="スパゲッティー店。 新店の オープニング 正社員を募集!", area="埼玉県 さいたま市 浦和区 高砂 / 浦和駅 徒歩2分"), "u", st)
        self.assertIsNotNone(r)
        self.assertEqual(r["store_name"], "洋麺屋五右衛門 浦和駅前店")
        st2 = crawl.new_site_stats()
        self.assertIsNone(crawl.classify_item(item(title="介護", store="ひまわり", snippet="新店舗 続々"), "u", st2))

    def test_candidate(self):
        st = crawl.new_site_stats()
        r = crawl.classify_item(item(title="ホール「2026年11月OPEN」", store="福恩麻辣湯 北浦和駅前店", area="埼玉県 さいたま市 浦和駅"), "u", st)
        self.assertEqual(r["store_name"], "福恩麻辣湯 北浦和駅前店")
        self.assertEqual(r["ward_status"], "浦和区")
        self.assertEqual(st["candidates"], 1)


class TestUrls(unittest.TestCase):
    def test_baitoru_urls(self):
        u = crawl.baitoru_urls(3)
        self.assertEqual(len(u), 3)
        self.assertTrue(u[1].endswith("/srt2/page2/"))

    def test_kyujinbox_url_encoded_without_prefecture(self):
        u = crawl.kyujinbox_url("新規オープン", 2)
        self.assertTrue(u.startswith("https://xn--pckua2a7gp15o89zb.com/%E6%96%B0"))
        self.assertTrue(u.endswith("?pg=2"))
        self.assertNotIn("%E5%9F%BC%E7%8E%89", u)  # 「埼玉」を入れない


class TestRobots(unittest.TestCase):
    ROBOTS = "User-agent: *\nDisallow: /jb/\nDisallow: /*ja=\nDisallow: /api/\n\nUser-agent: Baiduspider\nDisallow: /\n"

    def test_parse_and_match(self):
        pats = parse_robots_disallow(self.ROBOTS)
        self.assertEqual(pats, ["/jb/", "/*ja=", "/api/"])
        self.assertTrue(is_path_forbidden("/jb/abc", pats))
        self.assertTrue(is_path_forbidden("/x?ja=1", pats))
        self.assertFalse(is_path_forbidden("/%E3%82%AA?pg=2", pats))

    def test_config_forbidden_prefixes_block_detail_pages(self):
        pats = crawl.CONFIG["kyujinbox"]["forbidden_prefixes"]
        for p in ("/jb/x", "/rd/x", "/api/x"):
            self.assertTrue(is_path_forbidden(p, pats))

    def test_request_budget(self):
        self.assertLessEqual(crawl.CONFIG["max_requests_per_run"], 50)
        self.assertLessEqual(crawl.CONFIG["max_detail_fetches"], 15)
        pats = crawl.CONFIG["kyujinbox"]["forbidden_prefixes"]
        for p in ("/jb/x", "/jbn/x", "/jbi/x"):  # 求人ボックスの詳細系は取らない
            self.assertTrue(is_path_forbidden(p, pats))
        self.assertGreaterEqual(crawl.CONFIG["request_interval_seconds"], 5)
        # 最大: robots 2 + バイトル5 + 求人ボックス3キーワード x 5ページ + 追加3キーワード x 2ページ + 詳細15 = 43
        kb = crawl.CONFIG["kyujinbox"]
        total = (2 + crawl.CONFIG["max_pages_per_list"] + crawl.CONFIG["max_pages_per_list"] * len(kb["keywords"])
                 + kb["extra_max_pages"] * len(kb["extra_keywords"]) + crawl.CONFIG["max_detail_fetches"])
        self.assertEqual(total, 43)
        self.assertLessEqual(total, crawl.CONFIG["max_requests_per_run"])


if __name__ == "__main__":
    unittest.main()


class TestExitCode(unittest.TestCase):
    """全サイト失敗→非ゼロ(stores.jsonは触らない)、一部成功→0。ネットワークには出ない。"""

    KYUJIN_HTML = ('<section class="p-result_card"><h2><a class="p-result_title_link">ホール「2026年11月OPEN」</a></h2>'
                   '<div class="p-result_companyName">テスト食堂 浦和店</div>'
                   '<div class="p-result_area">埼玉県 さいたま市 浦和区 高砂 / 浦和駅 徒歩1分</div>'
                   '<div class="p-result_lines">仕事内容</div></section>')

    def run_main(self, fake_get, ci=None):
        """crawl.main を FakeFetcher で実行する。戻り値: (終了コード, stores.jsonの中身, 要求したURLの一覧, summary)。
        ci=True で GITHUB_ACTIONS=true、ci=False/None で未設定にする"""
        import tempfile
        urls: list[str] = []

        class FakeFetcher:
            count = 0

            def __init__(self, *a, **k):
                pass

            def get(self, url, forbidden=None):
                urls.append(url)
                return fake_get(url)

        env_keys = ("URAWA_OFFLINE_HTML_DIR", "GITHUB_ACTIONS")
        saved_env = {k: os.environ.pop(k, None) for k in env_keys}
        if ci:
            os.environ["GITHUB_ACTIONS"] = "true"
        orig = (crawl.Fetcher, crawl.STORES_PATH, crawl.SUMMARY_PATH, crawl.CONFIG["kyujinbox"]["keywords"],
                crawl.CONFIG["kyujinbox"]["extra_keywords"])
        with tempfile.TemporaryDirectory() as tmp:
            crawl.Fetcher = FakeFetcher
            crawl.STORES_PATH = Path(tmp) / "stores.json"
            crawl.SUMMARY_PATH = Path(tmp) / "run_summary.json"
            crawl.STORES_PATH.write_text("[]", encoding="utf-8")
            crawl.CONFIG["kyujinbox"]["keywords"] = ["新規オープン"]
            crawl.CONFIG["kyujinbox"]["extra_keywords"] = []
            try:
                code = crawl.main()
                stores_text = crawl.STORES_PATH.read_text(encoding="utf-8")
                summary = json.loads(crawl.SUMMARY_PATH.read_text(encoding="utf-8"))
            finally:
                (crawl.Fetcher, crawl.STORES_PATH, crawl.SUMMARY_PATH, crawl.CONFIG["kyujinbox"]["keywords"],
                 crawl.CONFIG["kyujinbox"]["extra_keywords"]) = orig
                os.environ.pop("GITHUB_ACTIONS", None)
                for k, v in saved_env.items():
                    if v is not None:
                        os.environ[k] = v
        return code, stores_text, urls, summary

    def test_all_blocked_is_nonzero_and_keeps_stores(self):
        def blocked(url):
            raise crawl.BlockedError("HTTP 403")
        code, stores_text, _, _ = self.run_main(blocked)
        self.assertEqual(code, 1)
        self.assertEqual(stores_text, "[]")

    def test_all_pages_empty_is_nonzero(self):
        code, _, _, _ = self.run_main(lambda url: "<html></html>")
        self.assertEqual(code, 1)

    def test_partial_success_is_zero_and_saved(self):
        def partial(url):
            if "baitoru.com" in url:
                raise crawl.BlockedError("HTTP 403")
            return self.KYUJIN_HTML if "pg=" not in url else "<html></html>"
        code, stores_text, _, _ = self.run_main(partial)
        self.assertEqual(code, 0)
        self.assertIn("テスト食堂 浦和店", stores_text)

    def kyujin_ok(self, url):
        if "baitoru.com" in url:
            raise AssertionError("baitoru にリクエストが出た: " + url)
        return self.KYUJIN_HTML if "pg=" not in url else "<html></html>"

    def test_ci_skips_baitoru_entirely(self):
        code, stores_text, urls, summary = self.run_main(self.kyujin_ok, ci=True)
        self.assertFalse([u for u in urls if "baitoru.com" in u])  # robots.txt を含め1件も出ない
        self.assertTrue(urls)  # 求人ボックスは取る
        self.assertEqual(summary["sites"]["baitoru"]["status"], "skipped_in_ci")
        self.assertEqual(summary["sites"]["baitoru"]["items_parsed"], 0)
        self.assertEqual(code, 0)
        self.assertIn("テスト食堂 浦和店", stores_text)
        self.assertFalse(summary["all_failed"])

    def test_without_ci_baitoru_is_fetched(self):
        def get(url):
            return "<html></html>"
        _, _, urls, summary = self.run_main(get, ci=False)
        self.assertTrue([u for u in urls if "baitoru.com" in u])
        self.assertNotEqual(summary["sites"]["baitoru"]["status"], "skipped_in_ci")

    def test_ci_all_failed_judged_without_skipped_site(self):
        # 求人ボックスが0件ならスキップしたバイトルを除いて失敗(非ゼロ、stores.jsonは不変)
        code, stores_text, urls, summary = self.run_main(lambda url: "<html></html>", ci=True)
        self.assertEqual(code, 1)
        self.assertEqual(stores_text, "[]")
        self.assertTrue(summary["all_failed"])
        self.assertFalse([u for u in urls if "baitoru.com" in u])
