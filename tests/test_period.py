#!/usr/bin/env python3
"""開店時期の正規化のユニットテスト(HTML不要)。実行日は 2026-10-24 に固定。"""

import datetime
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import period  # noqa: E402

TODAY = datetime.date(2026, 10, 24)


def n(text):
    return period.normalize_open_period(text, TODAY)


class TestExamples(unittest.TestCase):
    def check(self, text, label, precision, start, estimated=False, past=False):
        r = n(text)
        self.assertEqual(r["period"], label, text)
        self.assertEqual(r["precision"], precision, text)
        self.assertEqual(r["start"], start, text)
        self.assertEqual(r["estimated"], estimated, text)
        self.assertEqual(r["past"], past, text)

    def test_ten_days(self):
        self.check("12月上旬OPEN", "2026年12月上旬", "ten_days", "2026-12-01")
        self.check("12月中旬予定", "2026年12月中旬", "ten_days", "2026-12-11")
        self.check("12月下旬オープン", "2026年12月下旬", "ten_days", "2026-12-21")
        self.check("12月初旬オープン", "2026年12月上旬", "ten_days", "2026-12-01")
        self.check("12月中頃オープン予定", "2026年12月中旬", "ten_days", "2026-12-11")

    def test_day(self):
        self.check("11月24日OPEN", "2026年11月24日", "day", "2026-11-24")
        self.check("12月23日OPEN", "2026年12月23日", "day", "2026-12-23")
        self.check("2026年11月1日新規開院", "2026年11月1日", "day", "2026-11-01")
        self.check("11/24OPEN", "2026年11月24日", "day", "2026-11-24")
        self.check("2026年10月01日オープン", "2026年10月1日", "day", "2026-10-01")

    def test_month_with_era_and_width(self):
        self.check("令和9年4月に開園", "2027年4月", "month", "2027-04-01")
        self.check("２０２７年１月新規オープン", "2027年1月", "month", "2027-01-01")
        self.check("2027年10月オープン予定", "2027年10月", "month", "2027-10-01")
        self.check("2026年9月新規オープンに向けて", "2026年9月", "month", "2026-09-01")
        self.check("平成31年4月に新規開設したオープン", "2019年4月", "month", "2019-04-01", past=True)
        self.check("令和元年10月オープン", "2019年10月", "month", "2019-10-01", past=True)
        self.check("2027年4月頃オープン", "2027年4月", "month", "2027-04-01")

    def test_season(self):
        self.check("今秋オープン", "2026年秋", "season", "2026-09-01")
        self.check("2026年秋頃オープン予定", "2026年秋", "season", "2026-09-01")
        self.check("2026年夏オープン", "2026年夏", "season", "2026-06-01", past=True)
        self.check("来春OPEN", "2027年春", "season", "2027-03-01")
        self.check("2027年冬オープン", "2027年冬", "season", "2027-12-01")
        self.check("今夏オープン", "2026年夏", "season", "2026-06-01", past=True)

    def test_year_only(self):
        self.check("2027年開園予定", "2027年", "year", "2027-01-01")
        self.check("2026年開院予定", "2026年", "year", "2026-01-01")  # 年末までは過去にしない

    def test_month_only_inference(self):
        self.check("4月オープン", "2027年4月（推定）", "month", "2027-04-01", estimated=True)
        self.check("7月オープン", "2027年7月（推定）", "month", "2027-07-01", estimated=True)  # 3か月以上前は翌年に補う
        self.check("8月オープン", "2026年8月", "month", "2026-08-01", past=True)  # 過去2か月以内は開店済みの可能性
        self.check("9月オープン", "2026年9月", "month", "2026-09-01", past=True)
        self.check("10月オープン", "2026年10月", "month", "2026-10-01")  # 今月はまだ候補
        self.check("1月新規オープン", "2027年1月（推定）", "month", "2027-01-01", estimated=True)
        self.check("9月26日オープンの新しいお店", "2026年9月26日", "day", "2026-09-26", past=True)
        self.check("5月3日オープン", "2027年5月3日（推定）", "day", "2027-05-03", estimated=True)

    def test_past_expressions(self):
        self.check("2022年4月に新規オープンの施設です", "2022年4月", "month", "2022-04-01", past=True)
        self.check("2026年新規開院した駅チカクリニック", "2026年", "year", "2026-01-01", past=True)
        self.check("2026年11月にオープンしました", "2026年11月", "month", "2026-11-01", past=True)
        self.check("12月上旬にオープン済み", "2026年12月上旬", "ten_days", "2026-12-01", past=True)

    def test_unknown(self):
        for t in ("新店オープン", "新規オープン", "新店のオープニング正社員を募集", ""):
            r = n(t)
            self.assertEqual(r["period"], "時期不明（開店予定の募集あり）", t)
            self.assertEqual(r["precision"], "unknown")
            self.assertIsNone(r["start"])
            self.assertFalse(r["estimated"])

    def test_not_dates(self):
        for t in ("1年半で主任に", "オープン3年以内", "2年目で店長"):
            self.assertEqual(n(t)["precision"], "unknown", t)

    def test_year_boundary_next_year_inference(self):
        r = period.normalize_open_period("1月オープン", datetime.date(2026, 12, 20))
        self.assertEqual((r["period"], r["estimated"]), ("2027年1月（推定）", True))  # 年末に「1月」は翌年へ補う
