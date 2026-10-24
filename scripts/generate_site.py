#!/usr/bin/env python3
"""data/stores.json の publishable=true だけから、手元でプレビューする静的サイト docs/ を生成する。

公開前のサイト。noindex は config.json の `noindex`(既定 false)で切り替える(true のときだけ
<meta name="robots" content="noindex,nofollow"> を出す)。公開URLが未確定なので、ドメイン・GA4・
サイトマップ・canonical・OGP・CNAME はまだ設定しない(ドメイン確定後に追加する)。
画面には「仮サイト」とは出さない(サイト名の「（仮）」は config の値)。

コンビニポチ(conbini-pochi/scripts/generate_site.py)の作法に合わせた: Python標準ライブラリだけ、
テンプレートは文字列、外部CDN・外部スクリプトなし、ダークモードとスマホ表示はCSSだけ。
アイコンはポチシリーズ共通のキーキャップ「ポ」(pochi-branding/icon_svg.py の形を踏襲)を、
PNGではなくSVGで出す(色は config の site_color)。

読むのは stores.json の許可フィールド(ALLOWED_FIELDS)と、最終更新日用の run_summary.json の
run_at だけ。求人の文言はデータに存在しない前提で、それ以外のフィールドは使わない。
同じ入力と同じ「今日」なら、何度生成しても同じ結果になる。
"""

from __future__ import annotations

import argparse
import calendar
import datetime
import html
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA = ROOT / "data"
DOCS = ROOT / "docs"
JST = datetime.timezone(datetime.timedelta(hours=9))

# stores.json のうち、サイトで使ってよいフィールド。これ以外は読まない
ALLOWED_FIELDS = ("store_name", "location", "station", "open_period", "open_period_precision",
                  "open_period_start", "open_period_estimated", "sources", "first_seen", "last_seen",
                  "publishable")
SITE_LABEL = {"baitoru": "バイトル", "kyujinbox": "求人ボックス"}
UNKNOWN_PERIOD = "時期不明（開店予定の募集あり）"

NOTICE = "求人サイトの掲載内容から“オープン予定らしい”と判断した情報です。開店日・営業内容は店舗の公式発表をご確認ください。"


def esc(s) -> str:
    return html.escape(s or "", quote=True)


# ---------------------------------------------------------------------------
# データの整形(純粋関数)
# ---------------------------------------------------------------------------

def pick(store: dict) -> dict:
    return {k: store.get(k) for k in ALLOWED_FIELDS}


def load_publishable(path: Path) -> list[dict]:
    stores = json.loads(path.read_text(encoding="utf-8"))
    return [pick(s) for s in stores if s.get("publishable") is True]


def parse_date(s: str | None) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(s) if s else None
    except ValueError:
        return None


def add_months(d: datetime.date, n: int) -> datetime.date:
    idx = d.year * 12 + d.month - 1 + n
    y, m = divmod(idx, 12)
    return datetime.date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def display_place(store: dict) -> str:
    """「さいたま市浦和区高砂 / 浦和駅」→「浦和区高砂（浦和駅）」、町名なしなら「浦和駅周辺」。"""
    loc = store.get("location") or ""
    addr, _, st = (p.strip() for p in loc.partition("/"))
    addr = addr.replace("さいたま市", "").strip()
    if not st and store.get("station"):
        st = f"{store['station']}駅"
    if not addr:
        return f"{st}周辺" if st else "浦和区"
    return f"{addr}（{st}）" if st else addr


def group_of(store: dict, today: datetime.date) -> str:
    """near(今後2か月) / later / unknown。季節・年の精度は later。"""
    start = parse_date(store.get("open_period_start"))
    if start is None or store.get("open_period_precision") in (None, "unknown"):
        return "unknown"
    if store.get("open_period_precision") in ("season", "year"):
        return "later"
    return "near" if start <= add_months(today, CONFIG.get("near_months", 2)) else "later"


def sort_key(store: dict):
    return (store.get("open_period_start") or "9999-12-31", store.get("store_name") or "")


def is_new(store: dict, today: datetime.date) -> bool:
    fs = parse_date(store.get("first_seen"))
    return fs is not None and 0 <= (today - fs).days <= CONFIG.get("new_days", 7)


def is_stale(store: dict, today: datetime.date) -> bool:
    ls = parse_date(store.get("last_seen"))
    return ls is not None and (today - ls).days >= CONFIG.get("stale_days", 30)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def icon_svg(color: str) -> str:
    """キーキャップ「ポ」(small版)。フォントは埋め込まず、端末の日本語ゴシックで描く。"""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        f'<rect width="64" height="64" rx="14" fill="{color}"/>'
        '<rect x="4" y="5" width="56" height="56" rx="11" fill="#fff" fill-opacity=".35"/>'
        '<rect x="7" y="6" width="50" height="47" rx="8" fill="#fff"/>'
        '<text x="32" y="30" text-anchor="middle" dominant-baseline="central" '
        'font-family="Noto Sans JP, Hiragino Sans, Yu Gothic, Meiryo, sans-serif" font-weight="900" '
        f'font-size="44" fill="{color}">ポ</text></svg>'
    )


CSS = """
:root {
  --bg: #fafaf7; --card: #ffffff; --text: #1a1a1a; --muted: #6b6b6b;
  --accent: %(color)s; --line: #e5e2dc; --warn-bg: #fff6e5; --warn-line: #f0d9a8;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #14151a; --card: #1e2027; --text: #e8e8e6; --muted: #9a9a96;
    --line: #2c2e36; --warn-bg: #2a2418; --warn-line: #5a4a24; }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text);
  font-family: "Hiragino Sans", "Noto Sans JP", sans-serif; line-height: 1.6; }
a { color: inherit; }
header { padding: 24px 16px 12px; max-width: 980px; margin: 0 auto; }
header h1 a { text-decoration: none; font-size: 22px; display: inline-flex; align-items: center; gap: 8px; }
header h1 img { width: 32px; height: 32px; }
header p { color: var(--muted); font-size: 13px; margin-top: 4px; }
main { max-width: 980px; margin: 0 auto; padding: 8px 16px 48px; }
h2 { font-size: 18px; padding-left: 10px; border-left: 4px solid var(--accent); margin: 24px 0 12px; }
h2 .n { color: var(--muted); font-size: 13px; font-weight: 400; margin-left: 8px; }
.notice { background: var(--warn-bg); border: 1px solid var(--warn-line); border-radius: 10px;
  padding: 10px 14px; font-size: 13px; margin: 8px 0 12px; }
.summary { color: var(--muted); font-size: 13px; }
.grid { display: grid; gap: 10px; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); }
.item { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 12px; }
.item.stale { opacity: .6; }
.item .t { font-size: 15px; font-weight: 600; overflow-wrap: anywhere; }
.item .badge { display: inline-block; font-size: 10px; font-weight: 700; color: #fff; background: var(--accent);
  border-radius: 4px; padding: 1px 6px; margin-left: 6px; vertical-align: middle; }
.item .place { font-size: 13px; color: var(--muted); margin-top: 4px; }
.item .period { font-size: 14px; margin-top: 6px; font-weight: 600; }
.item .period .est { color: #b45309; font-weight: 700; background: #fff1d6; border-radius: 4px; padding: 0 4px; }
@media (prefers-color-scheme: dark) { .item .period .est { color: #fbbf24; background: #3a2e14; } }
.item .stale-note { font-size: 12px; color: var(--muted); margin-top: 6px; }
.item .src { margin-top: 8px; display: flex; flex-direction: column; gap: 4px; }
.item .src a { font-size: 12px; color: var(--accent); }
.empty { color: var(--muted); font-size: 14px; padding: 12px 0; }
footer { max-width: 980px; margin: 0 auto; padding: 16px; color: var(--muted); font-size: 12px; border-top: 1px solid var(--line); }
.doc h2 { margin-top: 28px; }
.doc p, .doc li { font-size: 14px; margin-top: 8px; }
.doc ul { padding-left: 1.4em; }
@media (max-width: 480px) { .grid { grid-template-columns: 1fr; } }
"""


def page(title: str, description: str, body: str, name: str) -> str:
    robots = '<meta name="robots" content="noindex,nofollow">\n' if CONFIG.get("noindex") else ""
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{robots}<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="icon" type="image/svg+xml" href="assets/icon.svg">
<link rel="stylesheet" href="style.css">
</head>
<body>
<header>
<h1><a href="index.html"><img src="assets/icon.svg" alt="" width="32" height="32">{esc(name)}</a></h1>
<p>{esc(CONFIG.get("site_tagline", ""))}</p>
</header>
<main>
{body}
</main>
<footer>
開店日・営業内容は店舗の公式発表をご確認ください。当サイトは各求人サイトの運営会社とは関係のない個人のサイトです。
｜ <a href="index.html">一覧</a> ｜ <a href="about.html">このサイトについて</a>
</footer>
</body>
</html>
"""


def render_period(store: dict) -> str:
    text = esc(store.get("open_period") or UNKNOWN_PERIOD)
    return text.replace("（推定）", '<span class="est">（推定）</span>')


def render_card(store: dict, today: datetime.date) -> str:
    stale = is_stale(store, today)
    badge = '<span class="badge">新着</span>' if is_new(store, today) else ""
    # 同じ求人サイトの出典は1つにまとめる(最後に確認できたものを優先)。サイト名は出現順
    best: dict[str, dict] = {}
    for src in store.get("sources") or []:
        site = src.get("site")
        if site in SITE_LABEL and (src.get("list_url") or "").startswith("https://"):
            if site not in best or (src.get("last_seen") or "") > (best[site].get("last_seen") or ""):
                best[site] = src
    links = []
    for site, src in best.items():
        url, label = src["list_url"], SITE_LABEL[site]
        links.append(f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer nofollow">'
                     f'求人サイト（{esc(label)}）で見る</a>')
    src_html = f'<div class="src">{"".join(links)}</div>' if links else ""
    stale_html = '<div class="stale-note">最近の求人では確認できませんでした</div>' if stale else ""
    return (f'<div class="item{" stale" if stale else ""}">'
            f'<div class="t">{esc(store.get("store_name"))}{badge}</div>'
            f'<div class="place">{esc(display_place(store))}</div>'
            f'<div class="period">開店時期: {render_period(store)}</div>'
            f'{stale_html}{src_html}</div>')


def updated_date(summary_path: Path, today: datetime.date) -> datetime.date:
    try:
        run_at = json.loads(summary_path.read_text(encoding="utf-8")).get("run_at", "")
        d = parse_date(run_at[:10])
        if d:
            return d
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return today


def render_index(stores: list[dict], today: datetime.date, updated: datetime.date) -> str:
    name = CONFIG.get("site_name", "浦和ポチ")
    groups = {"near": [], "later": [], "unknown": []}
    for s in sorted(stores, key=sort_key):
        groups[group_of(s, today)].append(s)
    heads = (("near", "近日中（今後2か月）"), ("later", "それ以降"), ("unknown", "時期不明"))
    sections = []
    for key, head in heads:
        items = groups[key]
        if not items:
            continue
        cards = "\n".join(render_card(s, today) for s in items)
        sections.append(f'<h2>{esc(head)}<span class="n">{len(items)}件</span></h2>\n<div class="grid">\n{cards}\n</div>')
    if not sections:
        sections.append('<p class="empty">いま載せられるお店はありません。</p>')
    body = (f'<div class="notice">{esc(NOTICE)}</div>\n'
            f'<p class="summary">{len(stores)}件 ｜ 最終更新: {updated.year}年{updated.month}月{updated.day}日</p>\n'
            + "\n".join(sections))
    return page(f"{name}｜{CONFIG.get('site_tagline', '')}", CONFIG.get("site_tagline", ""), body, name)


def render_about(name: str) -> str:
    body = f"""<div class="doc">
<h2>このサイトについて</h2>
<p>{esc(name)}は、さいたま市浦和区で新しくオープンしそうなお店を、求人サイトの情報からまとめたサイトです。</p>
<h2>仕組み</h2>
<p>求人サイトの一覧ページに載った情報から、店名・場所・開店時期だけを自動で抜き出して整理しています。開店時期は、書かれている表現を「2026年12月上旬」のような形にそろえたものです。年が書かれていない場合は推定で補い、「（推定）」と表示します。</p>
<h2>保存・表示していないもの</h2>
<ul>
<li>求人の本文・タイトル</li>
<li>画像</li>
<li>給与</li>
</ul>
<h2>出典</h2>
<p>各お店の「求人サイトで見る」は、求人サイトの一覧ページへのリンクです。求人の詳細は、そちらでご覧ください。</p>
<h2>ご注意</h2>
<ul>
<li>求人の掲載内容から“オープン予定らしい”と判断した情報で、不正確なことがあります。開店日・営業内容は店舗の公式発表をご確認ください。</li>
<li>各求人サイトの運営会社とは関係ありません。</li>
</ul>
</div>"""
    return page(f"このサイトについて｜{name}", "このサイトの仕組みと注意点", body, name)


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------

def build_files(stores: list[dict], today: datetime.date, updated: datetime.date) -> dict[str, str]:
    name = CONFIG.get("site_name", "浦和ポチ")
    color = CONFIG.get("site_color", "#C2185B")
    return {
        "index.html": render_index(stores, today, updated),
        "about.html": render_about(name),
        "style.css": CSS % {"color": color},
        "assets/icon.svg": icon_svg(color),
        ".nojekyll": "",  # GitHub Pages(legacy方式)でJekyll処理を避ける
    }


def write_site(files: dict[str, str], out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)  # docs/ は毎回作り直す
    for rel, text in files.items():
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", help="YYYY-MM-DD(省略時は今日。環境変数 URAWA_SITE_TODAY でも指定可)")
    args = ap.parse_args(argv)
    t = args.today or os.environ.get("URAWA_SITE_TODAY")
    today = parse_date(t) if t else datetime.datetime.now(JST).date()
    if today is None:
        print("--today は YYYY-MM-DD で指定してください", file=sys.stderr)
        return 2
    stores = load_publishable(DATA / "stores.json")
    files = build_files(stores, today, updated_date(DATA / "run_summary.json", today))
    write_site(files, DOCS)
    print(f"docs/ を生成しました({len(stores)}件)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
