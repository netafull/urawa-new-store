#!/usr/bin/env python3
"""scripts/generate_site.py のテスト。HTMLのフィクスチャは不要(合成データで検証)。今日は固定する。"""

import datetime
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import generate_site as g  # noqa: E402

TODAY = datetime.date(2026, 10, 24)


def store(name, period="2026年12月", start="2026-12-01", prec="month", est=False, loc="さいたま市浦和区高砂 / 浦和駅",
          first="2026-10-01", last="2026-10-24", publishable=True, sources=None):
    return {"key": "k-" + name, "store_name": name, "location": loc, "station": "浦和", "ward_status": "浦和区",
            "ward_basis": "x", "open_period": period, "open_period_precision": prec, "open_period_start": start,
            "open_period_estimated": est, "first_seen": first, "last_seen": last, "publishable": publishable,
            "publish_reason": "ok", "sources": sources or [{"site": "kyujinbox", "list_url": "https://example.test/a", "last_seen": last}]}


def render(stores):
    stores = [g.pick(s) for s in stores if s.get("publishable") is True]
    return g.build_files(stores, TODAY, TODAY)


def text_of(files):
    return "\n".join(files.values())


class TestGenerate(unittest.TestCase):
    def test_unpublishable_not_shown(self):
        files = render([store("出る店"), store("出ない店", publishable=False),
                        store("手揉み麺 与野日和", publishable=False), store("コジマ川口店", publishable=False)])
        t = text_of(files)
        self.assertIn("出る店", t)
        for n in ("出ない店", "与野日和", "コジマ川口店"):
            self.assertNotIn(n, t)

    def test_load_publishable_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "stores.json"
            p.write_text(json.dumps([store("A店"), store("B与野店", publishable=False),
                                     dict(store("C店"), publishable="yes")], ensure_ascii=False), encoding="utf-8")
            names = [s["store_name"] for s in g.load_publishable(p)]
        self.assertEqual(names, ["A店"])  # true 以外(区不明・文字列など)は載せない

    def test_noindex_default_off_and_switchable(self):
        self.assertIs(g.CONFIG.get("noindex"), False)  # 既定は出さない
        files = render([store("A店")])
        for page in ("index.html", "about.html"):
            self.assertNotIn("noindex", files[page])
        g.CONFIG["noindex"] = True
        try:
            files = render([store("A店")])
            for page in ("index.html", "about.html"):
                self.assertIn('<meta name="robots" content="noindex,nofollow">', files[page])
        finally:
            g.CONFIG["noindex"] = False

    def test_site_name_default_in_title_header_about(self):
        self.assertEqual(g.CONFIG["site_name"], "浦和新店舗情報（仮）")
        files = render([store("A店")])
        self.assertIn("<title>浦和新店舗情報（仮）｜", files["index.html"])
        self.assertIn("浦和新店舗情報（仮）</a></h1>", files["index.html"])
        self.assertIn("浦和新店舗情報（仮）", files["about.html"])
        self.assertIn("<title>このサイトについて｜浦和新店舗情報（仮）</title>", files["about.html"])

    def test_no_public_settings_or_external_resources(self):
        t = text_of(render([store("A店")]))
        for bad in ("canonical", "og:", "googletagmanager", "gtag", "sitemap", "adsbygoogle", "仮サイト", "公開前"):
            self.assertNotIn(bad, t)
        self.assertNotRegex(t, r'(?:src|href)="//|(?:src|href)="http://')
        self.assertNotIn("<script", t)

    def test_only_allowed_fields_used(self):
        s = store("A店")
        s.update({"snippet": "求人本文っぽい文言XYZ", "title": "求人タイトルQQQ", "ward_basis": "根拠ZZZ", "key": "keyKKK",
                  "publish_reason": "reasonRRR"})
        t = text_of(render([s]))
        for secret in ("求人本文っぽい文言XYZ", "求人タイトルQQQ", "根拠ZZZ", "keyKKK", "reasonRRR"):
            self.assertNotIn(secret, t)
        self.assertEqual(set(g.pick(s)), set(g.ALLOWED_FIELDS))

    def test_escaping(self):
        evil = '<script>alert(1)</script>&"\'店'
        files = render([store(evil, loc="さいたま市浦和区<b>町 / 浦和駅")])
        idx = files["index.html"]
        self.assertNotIn("<script>alert", idx)
        self.assertNotIn("<b>町", idx)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;&amp;&quot;&#x27;店", idx)

    def test_source_links_safe_and_deduped(self):
        srcs = [{"site": "kyujinbox", "list_url": "https://a.test/1", "last_seen": "2026-10-01"},
                {"site": "kyujinbox", "list_url": "https://a.test/2", "last_seen": "2026-10-20"},
                {"site": "baitoru", "list_url": "https://b.test/x?a=1&b=2", "last_seen": "2026-10-20"},
                {"site": "other", "list_url": "https://evil.test/", "last_seen": "2026-10-20"},
                {"site": "baitoru", "list_url": "javascript:alert(1)", "last_seen": "2026-10-21"}]
        idx = render([store("A店", sources=srcs)])["index.html"]
        self.assertEqual(idx.count("求人サイト（求人ボックス）で見る"), 1)
        self.assertEqual(idx.count("求人サイト（バイトル）で見る"), 1)
        self.assertIn('href="https://a.test/2"', idx)
        self.assertIn("https://b.test/x?a=1&amp;b=2", idx)
        self.assertNotIn("evil.test", idx)
        self.assertNotIn("javascript:", idx)
        self.assertEqual(idx.count('rel="noopener noreferrer nofollow"'), 2)
        self.assertEqual(idx.count('target="_blank"'), 2)

    def test_order_and_groups(self):
        stores = [store("後", start="2027-04-01", period="2027年4月"),
                  store("不明", period=g.UNKNOWN_PERIOD, start=None, prec="unknown"),
                  store("先", start="2026-11-01", period="2026年11月"),
                  store("中", start="2026-12-15", period="2026年12月中旬", prec="ten_days"),
                  store("秋", start="2026-09-01", period="2026年秋", prec="season"),
                  store("遠い月", start="2027-02-01", period="2027年2月")]
        idx = render(stores)["index.html"]
        pos = {n: idx.index(f'<div class="t">{n}') for n in ("先", "中", "秋", "遠い月", "後", "不明")}
        self.assertLess(pos["先"], pos["中"])  # 近日中は日付昇順
        self.assertLess(pos["中"], pos["秋"])  # 近日中 < それ以降
        self.assertLess(pos["秋"], pos["遠い月"])  # それ以降も日付昇順
        self.assertLess(pos["遠い月"], pos["後"])
        self.assertLess(pos["後"], pos["不明"])  # 時期不明は最後
        self.assertLess(idx.index("近日中"), idx.index("それ以降"))
        self.assertLess(idx.index("それ以降"), idx.index("時期不明"))
        self.assertEqual(g.group_of(g.pick(stores[3]), TODAY), "near")  # 12/15 は今後2か月以内
        self.assertEqual(g.group_of(g.pick(stores[4]), TODAY), "later")  # 季節は「それ以降」

    def test_empty_groups_hidden(self):
        idx = render([store("先", start="2026-11-01")])["index.html"]
        self.assertNotIn("それ以降", idx)
        self.assertNotIn("時期不明<", idx)

    def test_new_badge(self):
        idx = render([store("新しい", first="2026-10-20"), store("古い", first="2026-10-10")])["index.html"]
        self.assertEqual(idx.count('<span class="badge">新着</span>'), 1)
        self.assertRegex(idx, r"新しい<span class=\"badge\">新着</span>")
        self.assertTrue(g.is_new(g.pick(store("x", first="2026-10-17")), TODAY))   # ちょうど7日
        self.assertFalse(g.is_new(g.pick(store("x", first="2026-10-16")), TODAY))  # 8日

    def test_stale(self):
        idx = render([store("最近", last="2026-10-20"), store("古い店", last="2026-09-24")])["index.html"]
        self.assertEqual(idx.count("最近の求人では確認できませんでした"), 1)
        self.assertIn('class="item stale"', idx)
        self.assertFalse(g.is_stale(g.pick(store("x", last="2026-09-25")), TODAY))  # 29日
        self.assertTrue(g.is_stale(g.pick(store("x", last="2026-09-24")), TODAY))   # 30日

    def test_estimated_highlight(self):
        idx = render([store("推定店", period="2027年4月（推定）", start="2027-04-01", est=True)])["index.html"]
        self.assertIn('<span class="est">（推定）</span>', idx)

    def test_place_display(self):
        d = lambda loc: g.display_place({"location": loc, "station": "浦和"})
        self.assertEqual(d("さいたま市浦和区高砂 / 浦和駅"), "浦和区高砂（浦和駅）")
        self.assertEqual(d("さいたま市 / 浦和駅"), "浦和駅周辺")
        self.assertEqual(d("さいたま市浦和区元町"), "浦和区元町（浦和駅）")  # 駅フィールドで補う
        self.assertEqual(g.display_place({"location": "さいたま市浦和区", "station": ""}), "浦和区")

    def test_notice_count_and_updated(self):
        idx = render([store("A店"), store("B店")])["index.html"]
        self.assertIn(g.NOTICE, idx.replace("&quot;", '"'))
        self.assertIn("2件", idx)
        self.assertIn("最終更新: 2026年10月24日", idx)

    def test_updated_from_run_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "run_summary.json"
            p.write_text(json.dumps({"run_at": "2026-10-20T07:00:00+09:00"}), encoding="utf-8")
            self.assertEqual(g.updated_date(p, TODAY), datetime.date(2026, 10, 20))
            self.assertEqual(g.updated_date(Path(tmp) / "none.json", TODAY), TODAY)

    def test_deterministic(self):
        stores = [store("A店"), store("B店", start="2026-11-05")]
        self.assertEqual(render(stores), render(stores))

    def test_about_contents(self):
        about = g.render_about("浦和ポチ")
        for needle in ("店名・場所・開店時期だけ", "求人の本文・タイトル", "画像", "給与", "一覧ページへのリンク",
                       "不正確", "関係ありません"):
            self.assertIn(needle, about)
        self.assertNotRegex(about, r"@|https?://|mailto:")  # 連絡先は書かない
        for gone in ("問い合わせ", "未定"):
            self.assertNotIn(gone, about)  # 連絡先欄は設けない

    def test_write_site_recreates_docs(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "docs"
            out.mkdir()
            (out / "old.txt").write_text("x")
            g.write_site(render([store("A店")]), out)
            self.assertFalse((out / "old.txt").exists())
            for f in ("index.html", "about.html", "style.css", "assets/icon.svg", ".nojekyll"):
                self.assertTrue((out / f).is_file(), f)

    def test_site_name_configurable(self):
        orig = g.CONFIG["site_name"]
        g.CONFIG["site_name"] = "テスト名"
        try:
            self.assertIn("テスト名", render([store("A店")])["index.html"])
        finally:
            g.CONFIG["site_name"] = orig


if __name__ == "__main__":
    unittest.main()
