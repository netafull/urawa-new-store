#!/usr/bin/env python3
"""判定ルール(新店候補か/浦和区か/除外か)と、店舗の名寄せ。純粋関数のみ。"""

from __future__ import annotations

import hashlib
import re
import unicodedata

import period
from parsers import nfkc

# --- 新店シグナル -----------------------------------------------------------
# 「オープニング」は含めない(「オープニングスタッフ」は定型文で新店とは限らないため)。
STRONG_OPEN_RE = re.compile(r"オープン|OPEN|新店|開園|開校|開業|開院|新規出店", re.I)
# 「新規」単独は「新規フリー客」「新規採用」など別の意味が多すぎるので、開店を表す語と組のときだけ数える。
WEAK_OPEN_RE = re.compile(r"新規(?=出店|開設|店舗|開店)")
# 新店の根拠にならない定型句。見つけたら取り除いてから判定する。
OPEN_FALSE_PHRASES = re.compile(
    r"オープンキッチン|オープンポジション|オープンスペース|オープンな|オープンで|オープンカレッジ|オープンオフィス"
    r"|オープンマインド|オープンイノベーション"
    r"|オープン\s*\d+\s*年以内"  # 求人ボックスの特徴タグ「オープン3年以内」
    r"|(?:RENEWAL|リニューアル|リブランド)\s*(?:GRAND\s*)?(?:OPEN|オープン)"  # 既存店の改装
    r"|(?:新店舗?|新規オープン|オープン|NEW\s*OPEN)\s*続々|(?:続々|次々)\s*(?:と)?\s*(?:新店舗?|オープン)"
    r"|年間\s*\d+[^\s]{0,8}(?:オープン|出店)"
    r"|\d+\s*(?:店舗|教室|箇所|ヶ所|拠点)\s*(?:を)?\s*(?:新規\s*)?オープン"  # 会社全体の出店数の話
    r"|[~〜～]\s*(?:OPEN|オープン)|(?:OPEN|オープン)\s*[~〜～]\s*\d"  # 営業時間「15時~OPEN」
    r"|(?:オープン|OPEN)\s*(?:前|時間|時)"
    r"|(?<![月日年/\d])\d{1,3}\s*(?:OPEN|オープン)"  # 「16OPEN」のような数字+OPENは開店時期ではない
    r"|今後(?:も|は)?[^。!！]{0,14}?(?:出店|開院|オープン|OPEN|開業)"  # 会社の今後の計画
    r"|20\d{2}年も\s*(?:NEW\s*)?(?:OPEN|オープン)|今年も\s*(?:NEW\s*)?(?:OPEN|オープン)", re.I)  # 会社の宣伝文

DATE_TOKEN_RE = re.compile(r"\d+\s*月|(?<!年間)\d+\s*年(?!以内|間)|上旬|中旬|下旬|予定|今秋|今春|今冬|今夏|来春|来月")

# --- 除外 -------------------------------------------------------------------
# (理由, パターン)。タイトルと店名欄だけを見る(求人ボックスのスニペットは定型句が多くノイズになるため)。
EXCLUDE_RULES = [
    ("security_guidance", re.compile(r"警備|交通誘導|車両誘導|誘導スタッフ|誘導員")),
    ("exam", re.compile(r"試験|模試|試験監督")),
    ("event", re.compile(r"イベント|催事|フェス|物産展|ライブ|LIVE|ポップアップ|期間限定", re.I)),
    ("staff_increase", re.compile(r"増員|欠員|補充|入替|入れ替え|交替要員")),
    ("short_term", re.compile(r"短期|単発|1日だけ|1日限り|スキマバイト|スポット")),
]
DATE_RANGE_RE = re.compile(r"\d{1,2}/\d{1,2}\s*[～~\-ー]\s*\d{1,2}/\d{1,2}")
DAILY_PAY_RE = re.compile(r"日払い|即日払い|週払い")


def has_store_specific_new(text: str) -> bool:
    """「新店のオープニング正社員を募集」のように、その店が新店だと言い切る句(日付なしでも拾う)。
    「新店舗」は会社の一般論(新店舗続々など)が多いので対象外。"""
    t = OPEN_FALSE_PHRASES.sub("", nfkc(text)).replace(" ", "")
    return bool(re.search(r"新店(?!舗)", t))


def has_open_signal(text: str) -> bool:
    t = OPEN_FALSE_PHRASES.sub("", nfkc(text))
    t_nospace = t.replace(" ", "")
    return bool(STRONG_OPEN_RE.search(t_nospace) or WEAK_OPEN_RE.search(t_nospace))


def exclusion_reason(title: str, store_field: str, excerpt: str) -> str | None:
    text = nfkc(f"{title} {store_field}")
    for reason, rx in EXCLUDE_RULES:
        if rx.search(text):
            return reason
    if DATE_RANGE_RE.search(text):
        return "date_range_short"
    # 日払い「だけ」が売りのものは外す。開店時期(月・上旬など)が読めるなら残す。
    if DAILY_PAY_RE.search(text) and not DATE_TOKEN_RE.search(excerpt or ""):
        return "daily_pay_only"
    return None


# --- 開店時期の抜粋 ---------------------------------------------------------
_SPLIT_RE = re.compile(r"[\\¥＼／/【】「」『』★☆♪!！。\n<>＜＞()（）\[\]…|｜、,]+")


def open_segments(*texts: str) -> list[str]:
    """開店語を含む句を、開店時期らしさの順に並べて返す(判定に使うだけで保存しない。
    求人ボ ックスの強調で入る空白は詰める)。"""
    candidates: list[tuple[int, str]] = []
    for pri, text in enumerate(texts):
        text = OPEN_FALSE_PHRASES.sub("", nfkc(text))
        for seg in _SPLIT_RE.split(text):
            seg = seg.strip(" .・:：-－")
            if not seg:
                continue
            m = STRONG_OPEN_RE.search(seg.replace(" ", "")) or WEAK_OPEN_RE.search(seg.replace(" ", ""))
            if not m:
                continue
            score = (2 if DATE_TOKEN_RE.search(seg) else 0) + (1 if STRONG_OPEN_RE.search(seg) else 0)
            candidates.append((-score * 10 + pri, re.sub(r"(?<=[^\x00-\x7f]) | (?=[^\x00-\x7f])", "", seg)))
    candidates.sort(key=lambda x: x[0])
    return [c[1] for c in candidates]


def extract_excerpt(*texts: str, limit: int | None = 30) -> str:
    """最有力の句(判定の内部用。保存しない)。"""
    segs = open_segments(*texts)
    if not segs:
        return ""
    seg = segs[0]
    if limit is None or len(seg) <= limit:
        return seg
    m = STRONG_OPEN_RE.search(seg) or WEAK_OPEN_RE.search(seg)
    start = max(0, (m.start() if m else 0) - limit // 2)
    return seg[start:start + limit]


def open_period_from(texts: list[str], today) -> dict:
    """句の候補から開店時期を正規化する。最も精度の高い結果を採る(同精度なら先頭)。
    戻り値は period.normalize_open_period と同じ形(+ phrase_key: 名寄せ用の一時値。保存しない)。"""
    segs = open_segments(*texts)
    results = [period.normalize_open_period(sg, today) for sg in segs]
    best = None
    for r in results:
        if r["precision"] != "unknown" and (best is None or period.RANK[r["precision"]] > period.RANK[best["precision"]]):
            best = r
    if best is None:
        best = results[0] if results else period.normalize_open_period("", today)
        if any(r["past"] for r in results):
            best = dict(best, past=True)
    out = dict(best)
    out["phrase_key"] = normalize_name(segs[0]) if segs else ""
    return out


# --- 店名 -------------------------------------------------------------------
COMPANY_RE = re.compile(r"株式会社|有限会社|合同会社|\(株\)|㈱|法人|ホールディングス|グループ$|コーポレーション|ヒト・コミュニケーションズ|人材|派遣|本社|運営事業部|採用担当|【\d{3}】")
QUOTED_RE = re.compile(r"[「『]([^「」『』]{2,25})[」』]")


def extract_store_name(store_field: str) -> str:
    """店名欄から店舗名を取る。会社名らしければ空。「」で囲まれた店名があればそれを使う。"""
    f = nfkc(store_field)
    if not f:
        return ""
    if OPEN_PLUS_Q.search(f):
        q = QUOTED_RE.search(f)
        if q:
            return q.group(1).strip()
    if COMPANY_RE.search(f):
        return ""
    # 店名欄に宣伝文が入っているケース(「！」や開店語を含む長い文)は店名として使わない
    if len(f) > 30 or re.search(r"[！!]|スタッフ|募集", f) or (OPEN_PLUS_Q.search(f) and len(f) > 20):
        return ""
    return f


OPEN_PLUS_Q = re.compile(r"オープン|OPEN|新店|開園|開校", re.I)

# --- 浦和区判定 -------------------------------------------------------------
URAWA_STATIONS = {"浦和", "北浦和"}
# 浦和区外として確定している駅
NON_URAWA_STATIONS = {"武蔵浦和", "中浦和", "東浦和", "浦和美園", "南浦和", "西浦和", "与野", "北与野",
                       "与野本町", "さいたま新都心", "大宮", "土呂", "東大宮", "日進", "川口", "戸田", "蕨",
                       "北戸田", "戸田公園", "岩槻", "指扇", "西大宮"}
# 店名にこれが入っていたら、浦和区かどうか疑わしい
NON_URAWA_NAME_TOKENS = ("与野", "大宮", "武蔵浦和", "中浦和", "東浦和", "浦和美園", "南浦和", "新都心", "川口", "戸田", "蕨", "岩槻")

WARD_RE = re.compile(r"さいたま市\s*([^\s/]{1,5}区)")
STATION_RE = re.compile(r"([^\s/]+?)駅")


def parse_area(area: str) -> dict:
    a = nfkc(area)
    m = WARD_RE.search(a)
    ward = m.group(1) if m else ""
    # さいたま市の「町名」を拾って勤務地表記に使う(住所の一部)
    town = ""
    if m:
        after = a[m.end():].split("/")[0].strip()
        town = after if after and "駅" not in after else ""
    st = None
    for part in a.split("/")[-1:] if "/" in a else [a]:
        sm = STATION_RE.search(part)
        if sm:
            st = sm.group(1).split()[-1]
    if st is None:
        sm = STATION_RE.search(a)
        st = sm.group(1).split()[-1] if sm else ""
    address = f"さいたま市{ward}{town}".replace(" ", "") if ward else ("さいたま市" if "さいたま市" in a else "")
    return {"ward": ward, "station": st or "", "address": address}


def judge_ward(area: str, store_name: str = "") -> dict:
    """{status: urawa|unknown|out, basis, location, station}"""
    p = parse_area(area)
    ward, st = p["ward"], p["station"]
    loc_parts = [x for x in (p["address"], (st + "駅") if st else "") if x]
    location = " / ".join(loc_parts)
    a = nfkc(area)
    pref = re.search(r"(北海道|東京都|京都府|大阪府|[^\s]{2,3}県)", a)
    if pref and pref.group(1) != "埼玉県":
        return {"status": "out", "basis": f"住所が{pref.group(1)}", "location": location, "station": st}
    city = re.search(r"([^\s/]+?市)", a)
    if city and city.group(1) != "さいたま市" and not a.startswith("さいたま市") and "さいたま市" not in a:
        return {"status": "out", "basis": f"住所が{city.group(1)}", "location": location, "station": st}
    name_hit = next((t for t in NON_URAWA_NAME_TOKENS if t in (store_name or "")), None)
    if ward == "浦和区":
        if st in NON_URAWA_STATIONS:
            return {"status": "unknown", "basis": f"住所は浦和区だが最寄駅が{st}駅(浦和区外の駅)", "location": location, "station": st}
        if name_hit:
            return {"status": "unknown", "basis": f"住所は浦和区だが店名に「{name_hit}」を含む", "location": location, "station": st}
        return {"status": "urawa", "basis": "住所に「さいたま市浦和区」", "location": location, "station": st}
    if ward:
        return {"status": "out", "basis": f"住所が{ward}", "location": location, "station": st}
    if st in URAWA_STATIONS:
        if name_hit:
            return {"status": "unknown", "basis": f"最寄駅は{st}駅だが店名に「{name_hit}」を含む", "location": location, "station": st}
        return {"status": "urawa", "basis": f"最寄駅が{st}駅(浦和区として扱う)", "location": location, "station": st}
    if st in NON_URAWA_STATIONS:
        return {"status": "out", "basis": f"最寄駅が{st}駅(浦和区外)", "location": location, "station": st}
    return {"status": "unknown", "basis": "住所・最寄駅から区を判定できない", "location": location, "station": st}


# --- 名寄せ -----------------------------------------------------------------
_STRIP_RE = re.compile(r"[\s・･\-－—~〜～!！?？()（）\[\]【】「」『』★☆♪_＿.,、。]")


def normalize_name(name: str) -> str:
    """全角半角・空白・記号・末尾の「店」の有無をそろえる。"""
    n = unicodedata.normalize("NFKC", name or "").casefold()
    n = _STRIP_RE.sub("", n)
    if n.endswith("店") and len(n) > 2:
        n = n[:-1]
    return n


def normalize_station(st: str) -> str:
    s = unicodedata.normalize("NFKC", st or "").replace("駅", "").strip()
    return s


def store_key(name: str, station: str, tag: str = "") -> str:
    """名寄せキー。店名なしのときは tag(判定に使った句の正規化形)のハッシュを使う。
    句そのものは保存しない(ハッシュだけが key に入る)。"""
    n = normalize_name(name)
    s = normalize_station(station)
    if n:
        return f"{n}|{s}"
    h = hashlib.sha1(normalize_name(tag).encode("utf-8")).hexdigest()[:8]
    return f"(店名不明)|{s}|{h}"


# --- 公開してよいか(表示可否) ----------------------------------------------
CATEGORY_NAME_RE = re.compile(
    r"^(?:就労継続支援|就労移行支援|就労支援|放課後等デイサービス|放課後デイ|児童発達支援|訪問介護|訪問看護|デイサービス|グループホーム"
    r"|有料老人ホーム|ナーシングホーム|整体院|整骨院|クリニック|歯科|薬局|保育園|保育所|学童保育|学童|介護施設|障害福祉サービス)"
    r"[A-Za-zＡ-Ｚ0-9０-９型\s]*$")
JOB_TITLE_NAME_RE = re.compile(r"医師|看護師|薬剤師|保育士|歯科衛生士|スタッフ|募集|求人|正社員|アルバイト|パート|採用")
PROMO_NAME_RE = re.compile(r"[。!！?？♪★☆♡]")


def publish_judgement(store_name: str, ward_status: str) -> tuple[bool, str]:
    """(publishable, publish_reason)。公開表示してよい店名つきの浦和区レコードだけ True。"""
    name = nfkc(store_name)
    if not name:
        return False, "no_store_name"
    if ward_status != "浦和区":
        return False, "ward_unknown"
    if name.endswith("...") or name.endswith("…"):
        return False, "name_truncated"
    if CATEGORY_NAME_RE.match(name):
        return False, "name_is_category"
    if JOB_TITLE_NAME_RE.search(name):
        return False, "name_is_job_title"
    if PROMO_NAME_RE.search(name) or len(name) > 30:
        return False, "name_is_promo"
    return True, "ok"


# --- 他地域の話 -------------------------------------------------------------
_OTHER_CITY_RE = re.compile(r"(?<![ぁ-んァ-ヶー])((?!さいたま)[一-龥]{2,3})市(?![場内役])")
_OTHER_WARD_RE = re.compile(r"さいたま市(?!浦和区)[一-龥]{1,3}区")
_PREF_RE = re.compile(r"(北海道|東京都|京都府|大阪府|(?:青森|岩手|宮城|秋田|山形|福島|茨城|栃木|群馬|千葉|神奈川|新潟|富山|石川|福井|山梨|長野|岐阜|静岡|愛知|三重|滋賀|兵庫|奈良|和歌山|鳥取|島根|岡山|広島|山口|徳島|香川|愛媛|高知|福岡|佐賀|長崎|熊本|大分|宮崎|鹿児島|沖縄)県)")


def other_area_in_text(text: str) -> bool:
    """開店語を含む句の中に、さいたま市以外の都市・県名、または浦和区以外のさいたま市の区が
    明記されていれば True(「仙台市名坂…OPEN」「さいたま市緑区に新規オープン」など)。"""
    t = nfkc(text).replace(" ", "")
    return bool(_OTHER_CITY_RE.search(t) or _OTHER_WARD_RE.search(t) or _PREF_RE.search(t.replace("埼玉県", "")))
