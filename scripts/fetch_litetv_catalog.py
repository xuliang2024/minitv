#!/usr/bin/env python3
"""从 lite.tv 抓取 MiniTV 项目开发用的内容准备数据。

数据来源
    https://www.lite.tv          （站点的前端 SPA）
    https://www.lite.tv/api/v1/* （同一站点公开调用的 JSON API）

站点没有公开文档，接口来自前端包 https://www.lite.tv/static/js/index-*.js：
    POST /v1/auth/guest/login          访客登录，下发 jwt_user Cookie
    GET  /v1/home/categories           分类（标签）字典
    POST /v1/home/series               按分类取剧集列表
    GET  /v1/home/media_info           剧集详情（标题、简介、封面、分类、统计）
    GET  /v1/media_episode/list        分集列表（集号、是否免费、解锁类型）
    GET  /v1/media_episode/play        单集播放信息（时长、HLS 地址、上一集/下一集）
    GET  /v1/media_episode/ep_subtitle 分集字幕语言与地址
    GET  /v1/media/ocu/list            整剧解锁（金币价格阶梯）
    GET  /v1/media/popular_list        榜单（热度）
    GET  /v1/media/new_list            新剧
    GET  /v1/search/top/trending       热搜

所有请求都要带 `rs` 头（站点自己拼的客户端指纹串）和 jwt_user Cookie；
否则服务端返回 {"code": 80005, "error": "User does not exist"}。

产物
    data/litetv-catalog-<日期>/
        README.md            数据说明与字段字典
        manifest.json        抓取元信息、数量、分类覆盖、文件清单
        categories.json      站点分类字典（英文名 + 中文对照）
        tags.json            标签字典与 media_tag 值域
        dramas.json          100 部剧的完整结构化数据（含分集、解锁规则、字幕）
        dramas.csv           剧集主表（表格工具可直接打开）
        episodes.csv         分集表
        covers/              <media_id>_poster.jpg（竖版封面）、<media_id>_thumb.jpg（横版缩略图）
        subtitles/           <media_id>_ep<N>_en.srt 英文字幕样例
        raw/                 <media_id>.json 该剧的原始接口响应，便于追溯

用法
    .venv/bin/python scripts/fetch_litetv_catalog.py all --target 100
    .venv/bin/python scripts/fetch_litetv_catalog.py export      # 只用缓存重新导出
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import csv
import datetime as dt
import hashlib
import json
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

API_ROOT = "https://www.lite.tv/api"
SITE_ROOT = "https://www.lite.tv"
WEB_VERSION = "1.3.20"
FINGERPRINT = "f4a3b2c1d0e9f8a7b6c5d4e3f2a1b0c9"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 站点 /genre 页面下发的分类标签（label_type=1 为主分类，2 为细分标签）
GENRE_PAGE_TAGS = [
    "Drama", "Love After Marriage", "Fantasy", "Billionaire/CEO", "Rebirth",
    "Hidden Identity", "Sweet Love", "Twisted Love", "Mafia", "Family",
    "Strong Female", "Bitter Love", "Chasing Love", "Hidden Alias", "Office",
    "Rom-Com", "Body Swap", "Romance", "Revenge",
]

# 分类中英对照，用于项目后台的标签库
CATEGORY_ZH = {
    "Drama": "剧情",
    "Love After Marriage": "先婚后爱",
    "Fantasy": "奇幻",
    "Billionaire/CEO": "霸总",
    "Rebirth": "重生",
    "Hidden Identity": "隐藏身份",
    "Sweet Love": "甜宠",
    "Twisted Love": "虐恋",
    "Mafia": "黑帮",
    "Family": "家庭",
    "Strong Female": "大女主",
    "Bitter Love": "苦恋",
    "Chasing Love": "追妻",
    "Hidden Alias": "马甲",
    "Office": "职场",
    "Rom-Com": "喜剧爱情",
    "Body Swap": "灵魂互换",
    "Romance": "爱情",
    "Revenge": "复仇",
    "Tearjerker": "催泪",
    "Werewolf": "狼人",
    "Reunion": "破镜重圆",
    "Super Power": "超能力",
    "Vampire": "吸血鬼",
    "Thriller": "悬疑惊悚",
    "Tycoon": "豪门",
    "Forbidden Love": "禁忌恋",
    "Counterattack": "逆袭",
    "One Night Stand": "一夜情",
    "Contract Lover": "契约恋人",
    "Second Chance": "二次机会",
    "Mistaken Identity": "身份错认",
    "Underdog Victory": "草根逆袭",
    "Time Travel": "穿越",
    "Dual Identity": "双重身份",
    "Reverse Harem": "团宠",
    "Playing Dumb": "扮猪吃虎",
    "Hidden Bonds": "隐秘血缘",
    "Flash Marriage": "闪婚",
    "Age-Gap": "年龄差",
    "Painful Fate": "悲惨命运",
    "Love at Midlife": "中年爱情",
    "Family Bonds": "亲情羁绊",
    "Contract Marriage": "契约婚姻",
    "Campus": "校园",
    "Adorable Kid": "萌宝",
    "Action": "动作",
    "Substitute Bride": "替嫁",
    "Political Intrigue": "权谋",
    "Criminal": "犯罪",
    "Strong Duo": "双强",
    "Firefighter": "消防员",
    "Female Growth": "女性成长",
}

# 服务端 media_tag 是数字，前端包中未出现映射表，这里按出现情况标注为原始值
MEDIA_TAG_NOTE = "站点只下发 media_tag 数字，前端未带映射表；原样保留，语义待运营确认"

EN_STOPWORDS = set(
    "the a an and or of to in on for with her his he she they my your their our is are "
    "was were be been it its this that at as by from not but if you i we".split()
)
NON_LATIN = re.compile(r"[\u3000-\u9fff\uac00-\ud7af\u0400-\u04ff\u0600-\u06ff\u0e00-\u0e7f]")


# --------------------------------------------------------------------------- #
# HTTP 客户端
# --------------------------------------------------------------------------- #
class LiteTVError(RuntimeError):
    pass


class LiteTVClient:
    """带访客登录、限流重试和本地缓存的 lite.tv API 客户端。"""

    def __init__(self, cache_dir: Path, rps_pause: float = 0.12):
        self.cache_dir = cache_dir
        self.rps_pause = rps_pause
        self.jwt: str | None = None
        self._lock = threading.Lock()
        self._fail_streak = 0
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- 请求头 ---------------------------------------------------------- #
    @staticmethod
    def _rs_header() -> str:
        now = dt.datetime.now()
        ts = int(time.time() * 1000)
        return (
            f"web|2.0|{WEB_VERSION}|www.lite.tv|{FINGERPRINT}|1920|1080|en|rs|''|"
            f"{now:%Y-%m-%d %H:%M:%S}|5|''|{ts}|''|''"
        )

    def _raw(self, method: str, path: str, data=None, params=None,
             timeout: int = 40) -> dict:
        url = API_ROOT + path
        if params:
            url += "?" + urllib.parse.urlencode(
                {k: ("true" if v is True else v) for k, v in params.items() if v is not None}
            )
        body = json.dumps(data).encode() if data is not None else None
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json",
            "rs": self._rs_header(),
            "Origin": SITE_ROOT,
            "Referer": SITE_ROOT + "/genre",
            "User-Agent": UA,
        }
        with self._lock:
            jwt = self.jwt
        if jwt:
            headers["Cookie"] = f"jwt_user={jwt}"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return {"code": exc.code, "error": f"http {exc.code}", "_body": exc.read()[:300].decode("utf-8", "replace")}
        except Exception as exc:  # noqa: BLE001 - 网络异常统一重试
            return {"code": -1, "error": str(exc)}

    def login(self, attempts: int = 6) -> dict:
        last = None
        for i in range(attempts):
            resp = self._raw("POST", "/v1/auth/guest/login", {})
            last = resp
            payload = resp.get("data")
            if isinstance(payload, dict) and payload.get("value"):
                with self._lock:
                    self.jwt = payload["value"]
                return resp
            time.sleep(1.2 * (i + 1) + random.random())
        raise LiteTVError(f"访客登录失败: {str(last)[:200]}")

    def call(self, method: str, path: str, data=None, params=None, attempts: int = 5):
        if not self.jwt:
            self.login()
        last = None
        for attempt in range(attempts):
            resp = self._raw(method, path, data=data, params=params)
            code = resp.get("code")
            if code in (80005, 80001):  # 凭证过期：重新访客登录
                with self._lock:
                    self._fail_streak += 1
                    streak = self._fail_streak
                if streak >= 2:
                    try:
                        self.login()
                    except LiteTVError:
                        pass
                    with self._lock:
                        self._fail_streak = 0
                time.sleep(0.8 + attempt * 1.2)
                last = resp
                continue
            if code == 10000 or "data" in resp:
                with self._lock:
                    self._fail_streak = 0
                if self.rps_pause:
                    time.sleep(self.rps_pause * (0.5 + random.random()))
                return resp
            last = resp
            time.sleep(0.6 * (attempt + 1))
        return last if last is not None else {}

    # -- 带缓存的取数 ---------------------------------------------------- #
    def cached(self, key: str, method: str, path: str, data=None, params=None):
        fp = self.cache_dir / f"{key}.json"
        if fp.exists() and fp.stat().st_size > 20:
            try:
                return json.loads(fp.read_text())
            except json.JSONDecodeError:
                pass
        resp = self.call(method, path, data=data, params=params)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(json.dumps(resp, ensure_ascii=False))
        return resp

    def media_info(self, mid: int) -> dict:
        return self.cached(f"media_info/{mid}", "GET", "/v1/home/media_info",
                           params={"media_id": mid})

    def episode_list(self, mid: int) -> dict:
        return self.cached(f"episodes/{mid}", "GET", "/v1/media_episode/list",
                           params={"media_id": mid, "page_size": 300})

    def play(self, mid: int, ep: int) -> dict:
        return self.cached(f"play/{mid}_{ep}", "GET", "/v1/media_episode/play",
                           params={"media_id": mid, "episode_number": ep})

    def subtitles(self, mid: int, ep: int) -> dict:
        return self.cached(f"subtitle/{mid}_{ep}", "GET", "/v1/media_episode/ep_subtitle",
                           params={"media_id": mid, "episode_number": ep})

    def ocu(self, mid: int) -> dict:
        return self.cached(f"ocu/{mid}", "GET", "/v1/media/ocu/list",
                           params={"media_id": mid})

    def raw_get(self, key: str, path: str, params=None):
        return self.cached(key, "GET", path, params=params)


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def is_english(drama: dict) -> bool:
    text = f"{drama.get('title', '')} {drama.get('evaluate', '')[:400]}"
    if NON_LATIN.search(text):
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    latin = sum(1 for c in letters if ord(c) < 128)
    if latin / len(letters) < 0.98:
        return False
    words = re.findall(r"[a-zA-Z']+", text.lower())
    if not words:
        return False
    ratio = sum(1 for w in words if w in EN_STOPWORDS) / len(words)
    return ratio >= 0.07


def ts_to_iso(ts) -> str | None:
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return dt.datetime.fromtimestamp(ts, dt.timezone(dt.timedelta(hours=-8))).isoformat()


def popularity(drama: dict) -> float:
    return (
        float(drama.get("favorite_count") or 0) * 2.0
        + float(drama.get("like_count") or 0)
        + float(drama.get("cur_episodes") or 0) * 25.0
        + float(drama.get("rating") or 0)
    )


def category_names(drama: dict, label_type: int | None = None) -> list[str]:
    out = []
    for c in drama.get("categories") or []:
        if label_type is not None and c.get("label_type") != label_type:
            continue
        name = c.get("category_name")
        if name and name not in out:
            out.append(name)
    return out


def slug(text: str, limit: int = 48) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:limit] or "drama"


# --------------------------------------------------------------------------- #
# 阶段 1：字典与榜单
# --------------------------------------------------------------------------- #
def stage_taxonomy(client: LiteTVClient, ctx: dict) -> dict:
    cats = client.raw_get("taxonomy/categories", "/v1/home/categories")
    trending = client.raw_get("lists/trending", "/v1/search/top/trending")
    popular = client.raw_get("lists/popular", "/v1/media/popular_list",
                             params={"page": 1, "page_size": 50, "req_type": 0})
    new_list = client.raw_get("lists/new", "/v1/media/new_list",
                              params={"page": 1, "page_size": 50, "req_type": 0})
    recommend = client.raw_get("lists/recommend", "/v1/media_recommend/list")
    series = client.cached("lists/series_all", "POST", "/v1/home/series",
                           data={"category_ids": [], "page": 1, "page_size": 20, "not_in": []})

    xref: dict[str, list[int]] = {}
    for entry in (popular, new_list, recommend, series, trending):
        for mid in extract_mids(entry):
            xref.setdefault(str(mid), [])
    ctx["taxonomy"] = {
        "categories": cats.get("data") or [],
        "trending": trending.get("data") or {},
        "popular": popular.get("data") or {},
        "new_list": new_list.get("data") or {},
        "recommend": recommend.get("data") or [],
        "series_all": series.get("data") or {},
    }
    return ctx


def extract_mids(payload) -> list[int]:
    """从各种列表响应里抽出 media_id / mid。"""
    found: list[int] = []

    def walk(node):
        if isinstance(node, dict):
            for key in ("media_id", "mid"):
                if isinstance(node.get(key), int):
                    found.append(node[key])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found


# --------------------------------------------------------------------------- #
# 阶段 2：枚举 media_id 空间
# --------------------------------------------------------------------------- #
def stage_enumerate(client: LiteTVClient, ctx: dict, max_id: int, workers: int) -> dict:
    ids = list(range(1, max_id + 1))
    done = 0
    lock = threading.Lock()

    def fetch(mid: int) -> str:
        nonlocal done
        path = client.cache_dir / f"media_info/{mid}.json"
        if path.exists() and path.stat().st_size > 20:
            with lock:
                done += 1
            return "cached"
        resp = client.media_info(mid)
        with lock:
            done += 1
            if done % 100 == 0:
                print(f"    枚举进度 {done}/{len(ids)}", flush=True)
        data = resp.get("data") or {}
        return "ok" if data.get("title") else "empty"

    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        stats = Counter(pool.map(fetch, ids))
    print(f"  枚举完成: {dict(stats)}")
    return ctx


# --------------------------------------------------------------------------- #
# 阶段 3：挑选 100 部剧，覆盖不同分类
# --------------------------------------------------------------------------- #
def stage_select(client: LiteTVClient, ctx: dict, target: int, min_episodes: int) -> dict:
    pool, everything = [], []
    for path in sorted(client.cache_dir.glob("media_info/*.json"),
                       key=lambda p: int(p.stem)):
        resp = json.loads(path.read_text())
        drama = resp.get("data") or {}
        if not drama.get("title"):
            continue
        everything.append(drama)
        episodes = int(drama.get("total_episodes") or 0)
        released = int(drama.get("cur_episodes") or 0)
        if episodes < min_episodes or released < min_episodes:
            continue
        if not (drama.get("evaluate") or "").strip():
            continue
        if not is_english(drama):
            continue
        pool.append(drama)

    print(f"  有效内容 {len(everything)} 条，英文正片候选 {len(pool)} 部")

    by_id = {int(d["id"]): d for d in pool}
    taxonomy = ctx.get("taxonomy", {})
    showcase: list[int] = []
    for key in ("popular", "new_list", "series_all", "recommend", "trending"):
        for mid in extract_mids(taxonomy.get(key)):
            if mid in by_id and mid not in showcase:
                showcase.append(mid)

    picked: list[int] = []
    seen: set[int] = set()

    def take(mid: int) -> bool:
        if mid in seen or mid not in by_id:
            return False
        picked.append(mid)
        seen.add(mid)
        return True

    # 3.1 站点 /genre 与首页直接展示的剧优先保留
    for mid in showcase:
        if len(picked) >= target:
            break
        take(mid)
    print(f"  站点首页/榜单直接展示: {len(picked)} 部")

    # 3.2 每个 /genre 标签至少 2 部
    tag_members: dict[str, list[dict]] = defaultdict(list)
    for drama in pool:
        for name in set(category_names(drama)):
            tag_members[name].append(drama)
    for name in tag_members:
        tag_members[name].sort(key=popularity, reverse=True)

    rounds = 0
    while len(picked) < target and rounds < 3:
        rounds += 1
        for tag in GENRE_PAGE_TAGS:
            if len(picked) >= target:
                break
            for drama in tag_members.get(tag, []):
                if take(int(drama["id"])):
                    break

    # 3.3 用主分类轮转补齐，保证每类都有
    main_members: dict[str, list[dict]] = defaultdict(list)
    for drama in pool:
        mains = category_names(drama, 1) or category_names(drama, 2) or ["其他"]
        main_members[mains[0]].append(drama)
    for bucket in main_members.values():
        bucket.sort(key=popularity, reverse=True)

    guard = 0
    while len(picked) < target and guard < 200:
        guard += 1
        progressed = False
        for bucket in sorted(main_members.values(), key=len, reverse=True):
            if len(picked) >= target:
                break
            for drama in bucket:
                if take(int(drama["id"])):
                    progressed = True
                    break
        if not progressed:
            break

    # 3.4 兜底：全库按热度补齐
    for drama in sorted(pool, key=popularity, reverse=True):
        if len(picked) >= target:
            break
        take(int(drama["id"]))

    selected = [by_id[mid] for mid in picked]
    coverage = Counter()
    for drama in selected:
        for name in set(category_names(drama)):
            coverage[name] += 1
    missing = [t for t in GENRE_PAGE_TAGS if coverage.get(t, 0) == 0]
    print(f"  已选 {len(selected)} 部；/genre 标签覆盖 {len(coverage)} 个，缺 {missing or '无'}")
    ctx["selected"] = selected
    ctx["selected_ids"] = picked
    return ctx


# --------------------------------------------------------------------------- #
# 阶段 4：抓取分集、播放与解锁细节
# --------------------------------------------------------------------------- #
def stage_details(client: LiteTVClient, ctx: dict, workers: int) -> dict:
    ids = ctx["selected_ids"]
    jobs = []
    for mid in ids:
        jobs.append(("episodes", mid, None))
        jobs.append(("play", mid, 0))
        jobs.append(("ocu", mid, None))
        jobs.append(("subtitle", mid, 0))
    print(f"  拉取分集/解锁/字幕明细，共 {len(jobs)} 个任务")

    def run(job):
        kind, mid, ep = job
        try:
            if kind == "episodes":
                return client.episode_list(mid)
            if kind == "play":
                return client.play(mid, ep)
            if kind == "ocu":
                return client.ocu(mid)
            return client.subtitles(mid, ep)
        except Exception as exc:  # noqa: BLE001
            return {"code": -1, "error": str(exc)}

    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(run, jobs))

    # 用 play 结果补齐每一集的时长：play(N) 同时返回第 N-1 和 N+1 集
    episode_jobs = []
    for drama in ctx["selected"]:
        mid = int(drama["id"])
        total = int(drama.get("total_episodes") or 0)
        for ep in range(2, total + 1, 2):
            path = client.cache_dir / f"play/{mid}_{ep}.json"
            if not (path.exists() and path.stat().st_size > 20):
                episode_jobs.append((mid, ep))
    print(f"  需要补齐时长的分集请求 {len(episode_jobs)} 个")

    def run_play(job):
        mid, ep = job
        try:
            return client.play(mid, ep)
        except Exception as exc:  # noqa: BLE001
            return {"code": -1, "error": str(exc)}

    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(run_play, episode_jobs))
    return ctx


# --------------------------------------------------------------------------- #
# 阶段 5a：导出全站目录索引（能访问到的都留档）
# --------------------------------------------------------------------------- #
def build_catalog_index(client: LiteTVClient) -> list[dict]:
    rows = []
    for path in sorted(client.cache_dir.glob("media_info/*.json"), key=lambda p: int(p.stem)):
        drama = (json.loads(path.read_text()).get("data") or {})
        if not drama.get("title"):
            continue
        total = int(drama.get("total_episodes") or 0)
        released = int(drama.get("cur_episodes") or 0)
        covers = drama.get("covers") or []
        poster = next((c["cover"] for c in covers if c.get("cover_type") == 1), "")
        rows.append({
            "media_id": int(drama["id"]),
            "title": drama.get("title"),
            "is_full_drama": total >= 20 and released >= 20 and bool((drama.get("evaluate") or "").strip()),
            "language_guess": "en" if is_english(drama) else "other",
            "total_episodes": total,
            "published_episodes": released,
            "main_category": (category_names(drama, 1) or category_names(drama, 2) or [None])[0],
            "sub_categories": "|".join(category_names(drama, 2)),
            "favorite_count": drama.get("favorite_count"),
            "like_count": drama.get("like_count"),
            "rating": drama.get("rating"),
            "state": drama.get("state"),
            "publish_time": drama.get("publish_time"),
            "poster_url": poster,
            "description": (drama.get("evaluate") or "").strip(),
        })
    return rows


def write_catalog_index(rows: list[dict], out_dir: Path) -> None:
    (out_dir / "catalog_index.json").write_text(json.dumps(
        {
            "note": "站点可访问的全部 media_id 目录（比 /genre 页直接展示的 7 部多得多）",
            "count": len(rows),
            "items": rows,
        }, ensure_ascii=False, indent=2))
    columns = list(rows[0].keys()) if rows else []
    with (out_dir / "catalog_index.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


# --------------------------------------------------------------------------- #
# 阶段 5b：下载封面与字幕
# --------------------------------------------------------------------------- #
def download(url: str, dest: Path, referer: str = SITE_ROOT + "/") -> bool:
    if dest.exists() and dest.stat().st_size > 1024:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                blob = resp.read()
            if blob:
                dest.write_bytes(blob)
                return True
        except Exception:  # noqa: BLE001
            time.sleep(1.0 + attempt)
    return False


def stage_media(client: LiteTVClient, ctx: dict, out_dir: Path, workers: int) -> dict:
    covers_dir = out_dir / "covers"
    subs_dir = out_dir / "subtitles"
    jobs: list[tuple[str, str, Path]] = []
    for drama in ctx["selected"]:
        mid = int(drama["id"])
        slide = ctx["slides"].get(mid, {})
        for kind, url in slide.get("images", {}).items():
            if url:
                jobs.append(("image", url, covers_dir / f"{mid}_{kind}.jpg"))
        for name, url in slide.get("subtitles", {}).items():
            if url:
                jobs.append(("subtitle", url, subs_dir / f"{mid}_{name}.srt"))
    print(f"  下载 {len(jobs)} 个媒体文件（封面 / 字幕）")

    def run(job):
        _, url, dest = job
        return download(url, dest)

    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run, jobs))
    ok = sum(1 for r in results if r)
    print(f"  媒体文件成功 {ok}/{len(results)}")
    return ctx


# --------------------------------------------------------------------------- #
# 阶段 6：整理并写出交付数据
# --------------------------------------------------------------------------- #
def build_slide(client: LiteTVClient, drama: dict) -> dict:
    """把一部剧的所有接口响应整理成一条结构化记录。"""
    mid = int(drama["id"])
    info = drama
    episodes_raw = (client.episode_list(mid).get("data") or [])
    play0 = (client.play(mid, 0).get("data") or {})
    ocu = (client.ocu(mid).get("data") or {})
    subs0 = (client.subtitles(mid, 0).get("data") or {})

    ep_by_number: dict[int, dict] = {}
    for raw in episodes_raw:
        if not isinstance(raw, dict):
            continue
        ep_by_number[int(raw["episode_number"])] = dict(raw)

    # play 响应里的单集信息最全：时长、HLS、封面；相邻两集都能拿到
    for ep in range(0, int(info.get("total_episodes") or 0) + 1, 2):
        payload = client.play(mid, ep).get("data") or {}
        for slot in ("prev_episode", "cur_episode", "next_episode"):
            item = payload.get(slot)
            if isinstance(item, dict) and item.get("episode_number") is not None:
                number = int(item["episode_number"])
                merged = ep_by_number.setdefault(number, {})
                for key, value in item.items():
                    if value not in (None, "", [], {}):
                        merged[key] = value

    domain = play0.get("cover", {}).get("cover", "")
    media_domain = "https://es-w1.lite.tv"
    if play0.get("next_episode"):
        media_domain = play0["next_episode"].get("main_domain") or media_domain

    subtitle_map: dict[str, str] = {}
    for item in subs0.get("list") or []:
        lang = item.get("lang")
        if lang:
            subtitle_map[lang] = media_domain + item["url"]

    episodes = []
    for number in sorted(ep_by_number):
        raw = ep_by_number[number]
        hls480 = raw.get("p480") or ""
        hls720 = raw.get("p720") or ""
        episodes.append({
            "episode_number": number,
            "title": raw.get("title") or f"ep.{number}",
            "duration_sec": raw.get("duration") or 0,
            "is_free": bool(int(raw.get("is_free") or 0)),
            "fee": raw.get("fee") or 0,
            "unlock": raw.get("unlock") or 0,
            "is_sub": raw.get("is_sub") or 0,
            "video_url_hls_480": media_domain + hls480 if hls480 else "",
            "video_url_hls_720": media_domain + hls720 if hls720 else "",
            "cover_url": media_domain + raw["cover"] if raw.get("cover") else "",
        })

    free_numbers = [e["episode_number"] for e in episodes if e["is_free"]]
    free_num = play0.get("free_num")
    if not free_numbers and free_num:
        free_numbers = list(range(0, int(free_num)))

    covers = info.get("covers") or []
    poster_url = next((c["cover"] for c in covers if c.get("cover_type") == 1), "")
    if not poster_url:
        poster_url = (play0.get("cover") or {}).get("cover", "")
    thumb_url = (play0.get("little_cover") or {}).get("cover", "")
    if not thumb_url:
        thumb_url = next((c["cover"] for c in covers if c.get("cover_type") == 3), "")

    unlock_options = []
    for buy in ocu.get("buy_infos") or []:
        unlock_options.append({
            "unlock_episodes": buy.get("ocu_unlocked_num"),
            "coins": buy.get("ocu_coins"),
            "coins_original": buy.get("ocu_original_coins"),
            "discount_percent": buy.get("ocu_ratio"),
            "is_default": buy.get("is_default"),
        })

    return {
        "media_id": mid,
        "title": info.get("title"),
        "slug": slug(info.get("title") or ""),
        "description": (info.get("evaluate") or "").strip(),
        "language": "en",
        "site_url": f"{SITE_ROOT}/play?mid={mid}&eid=1",
        "classification": {
            "main_category": (category_names(info, 1) or category_names(info, 2) or [None])[0],
            "sub_categories": category_names(info, 2),
            "all_category_names": category_names(info),
            "categories": info.get("categories") or [],
            "category_rankings": sorted(
                [
                    {
                        "category_name": c.get("category_name"),
                        "category_id": c.get("category_id"),
                        "label_type": c.get("label_type"),
                        "rank": c.get("ranking"),
                    }
                    for c in info.get("categories") or []
                ],
                key=lambda x: (x["label_type"] or 0, x["rank"] or 0),
            ),
            "media_tags": info.get("tags") or [],
            "media_tag_values": sorted({t.get("media_tag") for t in info.get("tags") or [] if t.get("media_tag") is not None}),
            "media_tag_note": MEDIA_TAG_NOTE,
            "age_rating": None,
            "age_rating_note": "站点未下发年龄分级；项目内按 17+ 样例配置",
            "region": None,
            "region_note": "站点未下发地区；项目内按美国 / 加拿大样例配置",
            "free_episode_count": int(free_num or len(free_numbers)),
            "free_episode_numbers": free_numbers[:20],
            "coin_price_per_episode": ocu.get("price"),
            "coin_unlock_options": unlock_options,
        },
        "episodes": {
            "total_episodes": info.get("total_episodes"),
            "published_episodes": info.get("cur_episodes"),
            "is_complete": bool(play0.get("is_over")),
            "subtitle_languages": sorted(subtitle_map),
            "items": episodes,
        },
        "stats": {
            "play_count": play0.get("play_count"),
            "like_count": info.get("like_count"),
            "favorite_count": info.get("favorite_count"),
            "rating": info.get("rating"),
            "rating_note": "站点 rating 字段未启用，全部为 0；热度用 play/like/favorite 与分类排名代替",
        },
        "state": {
            "raw_state": info.get("state"),
            "status": state_label(info.get("state")),
            "publish_time": info.get("publish_time"),
            "release_time": ts_to_iso(play0.get("release_stamp")),
            "updated_time": ts_to_iso(play0.get("updated_ts")),
        },
        "covers": {
            "poster_source_url": poster_url,
            "thumbnail_source_url": thumb_url,
            "poster_file": f"covers/{mid}_poster.jpg" if poster_url else None,
            "thumbnail_file": f"covers/{mid}_thumb.jpg" if thumb_url else None,
        },
        "assets": {
            "subtitle_sample_urls": subtitle_map,
        },
    }, {
        "images": {"poster": poster_url, "thumb": thumb_url},
        "subtitles": {"ep0_en": subtitle_map.get("en", "")},
    }


def state_label(state) -> str:
    return {0: "已上架", 1: "已上架", 2: "草稿", 3: "已下架"}.get(state, f"未知({state})")


def stage_export(client: LiteTVClient, ctx: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "raw").mkdir(exist_ok=True)
    print("  整理结构化记录 ...")
    slides, slide_assets = [], {}
    for drama in ctx["selected"]:
        record, assets = build_slide(client, drama)
        slides.append(record)
        slide_assets[int(drama["id"])] = assets
    ctx["slides"] = slide_assets

    # 原始响应归档
    for record in slides:
        mid = record["media_id"]
        bundle = {
            "media_info": json.loads((client.cache_dir / f"media_info/{mid}.json").read_text()),
            "episode_list": json.loads((client.cache_dir / f"episodes/{mid}.json").read_text()),
            "play_examples": {},
            "ocu_list": json.loads((client.cache_dir / f"ocu/{mid}.json").read_text()),
            "subtitle_ep0": json.loads((client.cache_dir / f"subtitle/{mid}_0.json").read_text()),
        }
        for ep in (0, 1, 2):
            fp = client.cache_dir / f"play/{mid}_{ep}.json"
            if fp.exists():
                bundle["play_examples"][str(ep)] = json.loads(fp.read_text())
        (out_dir / "raw" / f"{mid}.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=1))

    ctx["slides_records"] = slides
    return ctx


def write_outputs(client: LiteTVClient, ctx: dict, out_dir: Path, target: int) -> None:
    taxonomy = ctx.get("taxonomy", {})
    slides = ctx["slides_records"]
    fetched_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")

    # categories.json
    seen: dict[tuple, dict] = {}
    for cat in taxonomy.get("categories", []):
        seen[(cat.get("label_type"), cat.get("category_name"))] = {
            "category_id": cat.get("id"),
            "rec_id": cat.get("rec_id"),
            "label_type": cat.get("label_type"),
            "label_type_name": {1: "主分类", 2: "细分标签"}.get(cat.get("label_type"), "其他"),
            "name_en": cat.get("label_name"),
            "name_zh": CATEGORY_ZH.get(cat.get("label_name", ""), ""),
            "in_genre_page": cat.get("label_name") in GENRE_PAGE_TAGS,
        }
    categories_doc = {
        "source": f"{API_ROOT}/v1/home/categories",
        "fetched_at": fetched_at,
        "note": "站点 /genre 页面下发的分类标签；label_type=1 为主分类，2 为细分标签",
        "genre_page_tags": GENRE_PAGE_TAGS,
        "items": sorted(seen.values(), key=lambda x: (x["label_type"] or 0, x["category_id"] or 0)),
    }
    (out_dir / "categories.json").write_text(json.dumps(categories_doc, ensure_ascii=False, indent=2))

    # tags.json：样本里实际出现的分类 + media_tag 值域
    usage = Counter()
    tag_values = Counter()
    for record in slides:
        for name in record["classification"]["all_category_names"]:
            usage[name] += 1
        for value in record["classification"]["media_tag_values"]:
            tag_values[value] += 1
    tags_doc = {
        "source": f"{API_ROOT}/v1/home/media_info",
        "fetched_at": fetched_at,
        "note": "项目标签库的种子数据：英文名 + 中文名 + 本样本关联剧数",
        "items": [
            {
                "name_en": name,
                "name_zh": CATEGORY_ZH.get(name, ""),
                "label_type": next((i["label_type"] for i in categories_doc["items"] if i["name_en"] == name), None),
                "in_genre_page": name in GENRE_PAGE_TAGS,
                "drama_count": count,
            }
            for name, count in usage.most_common()
        ],
        "media_tag_values": [
            {"value": value, "drama_count": count, "note": MEDIA_TAG_NOTE}
            for value, count in sorted(tag_values.items())
        ],
    }
    (out_dir / "tags.json").write_text(json.dumps(tags_doc, ensure_ascii=False, indent=2))

    # dramas.json
    dramas_doc = {
        "source": {
            "site": SITE_ROOT,
            "genre_page": f"{SITE_ROOT}/genre",
            "api": API_ROOT,
            "fetched_at": fetched_at,
            "method": "访客登录 + /v1/home/media_info 逐条抓取，脚本 scripts/fetch_litetv_catalog.py",
        },
        "count": len(slides),
        "items": slides,
    }
    (out_dir / "dramas.json").write_text(json.dumps(dramas_doc, ensure_ascii=False, indent=2))

    # dramas.csv
    with (out_dir / "dramas.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "media_id", "title", "main_category", "sub_categories", "total_episodes",
            "published_episodes", "free_episode_count", "coin_price_per_episode",
            "play_count", "like_count", "favorite_count", "rating", "status",
            "publish_time", "poster_file", "thumbnail_file", "site_url", "description",
        ])
        for r in slides:
            c = r["classification"]
            writer.writerow([
                r["media_id"], r["title"], c["main_category"], "|".join(c["sub_categories"]),
                r["episodes"]["total_episodes"], r["episodes"]["published_episodes"],
                c["free_episode_count"], c["coin_price_per_episode"],
                r["stats"]["play_count"], r["stats"]["like_count"], r["stats"]["favorite_count"],
                r["stats"]["rating"], r["state"]["status"], r["state"]["publish_time"],
                r["covers"]["poster_file"], r["covers"]["thumbnail_file"], r["site_url"],
                r["description"],
            ])

    # episodes.csv
    with (out_dir / "episodes.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "media_id", "title", "episode_number", "episode_title", "duration_sec",
            "is_free", "fee", "unlock_type", "hls_480", "hls_720", "cover_url",
        ])
        for r in slides:
            for ep in r["episodes"]["items"]:
                writer.writerow([
                    r["media_id"], r["title"], ep["episode_number"], ep["title"],
                    ep["duration_sec"], int(ep["is_free"]), ep["fee"],
                    "free" if ep["is_free"] else "coin_or_vip",
                    ep["video_url_hls_480"], ep["video_url_hls_720"], ep["cover_url"],
                ])

    # manifest.json
    cat_cov = Counter()
    for r in slides:
        for name in set(r["classification"]["all_category_names"]):
            cat_cov[name] += 1
    index_rows = build_catalog_index(client)
    write_catalog_index(index_rows, out_dir)
    files = sorted(
        str(p.relative_to(out_dir))
        for p in out_dir.rglob("*") if p.is_file() and p.name != "manifest.json"
    )
    manifest = {
        "name": "LiteTV 内容准备数据（MiniTV 项目）",
        "generated_at": fetched_at,
        "source_site": SITE_ROOT,
        "source_genre_page": f"{SITE_ROOT}/genre",
        "source_api": API_ROOT,
        "generator": "scripts/fetch_litetv_catalog.py",
        "requested_count": target,
        "delivered_count": len(slides),
        "catalog_index_count": len(index_rows),
        "catalog_index_full_dramas": sum(1 for r in index_rows if r["is_full_drama"]),
        "catalog_index_english_dramas": sum(
            1 for r in index_rows if r["is_full_drama"] and r["language_guess"] == "en"
        ),
        "counts": {
            "dramas": len(slides),
            "episodes": sum(len(r["episodes"]["items"]) for r in slides),
            "episodes_with_duration": sum(
                1 for r in slides for ep in r["episodes"]["items"] if ep["duration_sec"]
            ),
            "covers": len(list((out_dir / "covers").glob("*.jpg"))) if (out_dir / "covers").exists() else 0,
            "subtitles": len(list((out_dir / "subtitles").glob("*.srt"))) if (out_dir / "subtitles").exists() else 0,
        },
        "category_coverage": dict(sorted(cat_cov.items(), key=lambda kv: -kv[1])),
        "genre_page_tag_coverage": {t: cat_cov.get(t, 0) for t in GENRE_PAGE_TAGS},
        "media_ids": [r["media_id"] for r in slides],
        "fields": {
            "dramas.json": "剧集主记录：标题/简介/分类/封面/统计/解锁规则/分集/字幕语言",
            "dramas.csv": "剧集主表，便于导入表格或数据库",
            "episodes.csv": "分集表：集号/时长/免费标记/解锁类型/HLS 地址/单集封面",
            "catalog_index.csv": "站点可访问的全部 media_id 目录（含未入选的剧与非正片）",
            "categories.json": "站点分类字典（英文名 + 中文名）",
            "tags.json": "标签字典与 media_tag 值域",
            "covers/": "竖版封面与横版缩略图",
            "subtitles/": "第一集英文字幕样例（SRT）",
            "raw/": "每部剧的原始接口响应，便于追溯",
        },
        "files": files,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"  写出 manifest.json / dramas.json / dramas.csv / episodes.csv / categories.json / tags.json")


def write_readme(out_dir: Path, manifest: dict) -> None:
    cov = manifest["category_coverage"]
    top = "、".join(f"{k} {v}" for k, v in list(cov.items())[:12])
    readme = f"""# LiteTV 内容准备数据（MiniTV 项目）

抓取时间：{manifest['generated_at']}
数据来源：[lite.tv 分类页]({manifest['source_genre_page']}) 及其同站公开接口 `{manifest['source_api']}`
生成脚本：[scripts/fetch_litetv_catalog.py](../../scripts/fetch_litetv_catalog.py)

## 1. 这批数据解决什么问题

MiniTV 的开发、联调和演示需要真实形态的剧集数据：竖版封面、英文剧名与简介、
分类标签、总集数与已发布集数、免费集与金币解锁规则、单集时长、字幕语言。
本目录一次性备齐这些字段，后端可以直接导入作为种子数据，前端可以直接渲染列表和详情页。

共 {manifest['counts']['dramas']} 部剧、{manifest['counts']['episodes']} 集分集记录，
其中 {manifest['counts']['episodes_with_duration']} 集带时长，
{manifest['counts']['covers']} 张封面、{manifest['counts']['subtitles']} 个字幕文件。

> 说明：`/genre` 页面每次直接展示的只有 7 部，但站点接口的 media_id 空间里
> 可访问 {manifest['catalog_index_count']} 条内容，其中正片 {manifest['catalog_index_full_dramas']} 部、
> 英文正片 {manifest['catalog_index_english_dramas']} 部。因此这里交付
> {manifest['counts']['dramas']} 部完整明细，另外把全部 {manifest['catalog_index_count']} 条
> 目录索引一并留档（`catalog_index.csv` / `catalog_index.json`），能拿到的都没有丢。

## 2. 目录结构

```
{out_dir.name}/
├── README.md          本文件
├── manifest.json      抓取元信息、数量统计、分类覆盖、文件清单
├── categories.json    站点分类字典（英文名 + 中文名 + 是否出现在 /genre 页）
├── tags.json          标签字典、每个标签关联剧数、media_tag 值域
├── dramas.json        {manifest['counts']['dramas']} 部剧的完整结构化记录（主交付文件）
├── dramas.csv         剧集主表，可直接用 Excel / 数据库导入工具打开
├── episodes.csv       分集表（集号、时长、免费标记、解锁类型、HLS 地址）
├── catalog_index.csv  全站 {manifest['catalog_index_count']} 条可访问内容目录（含未入选的剧）
├── covers/            封面：<media_id>_poster.jpg（竖版）、<media_id>_thumb.jpg（横版）
├── subtitles/         第一集英文字幕样例（SRT）
└── raw/               每部剧的原始接口响应，字段有疑问时回查
```

## 3. 抓取方法

1. `POST /v1/auth/guest/login` 访客登录，拿到 `jwt_user` Cookie。
   站点前端会给每个请求加一个 `rs` 头（`web|2.0|版本|域名|指纹|宽|高|语言|...`），
   缺少它服务端会返回 `{{"code":80005,"error":"User does not exist"}}`。
2. `GET /v1/home/categories` 取分类字典；`GET /v1/media/popular_list`、`/v1/media/new_list`、
   `POST /v1/home/series` 取站点首页与分类页当前展示的剧。
3. `GET /v1/home/media_info?media_id=N` 遍历 media_id 空间，得到全部可访问剧集。
4. 只保留英文正片：总集数与已发布集数不少于 20、简介非空、正文为英文（排除同一部剧的
   日/韩/西/葡/印尼/泰语版本）。
5. 挑选 {manifest['counts']['dramas']} 部：先保站点首页与 `/genre` 直接展示的剧，
   再让每个 `/genre` 标签至少出现 2 部，最后按分类轮转补齐，兼顾热度。
6. 逐剧补抓分集列表、单集播放信息、整剧解锁价格、字幕语言；`/v1/media_episode/play?episode_number=N`
   会同时返回第 N-1 与 N+1 集，因此每隔一集请求一次即可覆盖全部分集时长。
7. 下载封面与第一集英文字幕。所有请求走本地缓存（`data/.cache/`），重复运行不会重复抓取。

## 4. 字段说明

### dramas.json → items[]

| 字段 | 含义 |
|---|---|
| `media_id` | 站点剧集 ID，可作为项目内种子数据的外键前缀 |
| `title` / `description` | 英文剧名 / 英文简介（详情页文案） |
| `classification.main_category` | 主分类（`label_type=1`），没有主分类时取第一个细分标签 |
| `classification.sub_categories` | 细分标签（`label_type=2`），客户端标签位用 |
| `classification.category_rankings` | 该剧在每个分类里的排名（榜单页的排名来源） |
| `classification.media_tag_values` | 站点 `media_tag` 原始值（语义未在前端暴露，待运营确认） |
| `classification.free_episode_count` | 免费集数，对应「前 N 集免费」的解锁规则 |
| `classification.coin_price_per_episode` | 单集金币价；`coin_unlock_options` 是整剧打包解锁阶梯 |
| `classification.age_rating` / `region` | 站点未下发，留空；项目按设计稿示例（17+、美国 / 加拿大）自行配置 |
| `episodes.total_episodes` / `published_episodes` | 总集数 / 已发布集数 |
| `episodes.items[]` | 分集：集号、时长、是否免费、解锁类型、HLS 480/720、单集封面 |
| `episodes.subtitle_languages` | 该剧第一集可用的字幕语言 |
| `stats.*` | 播放量、点赞、收藏；站点 `rating` 字段未启用（全 0），热度用这些字段与分类排名代替 |
| `state.*` | 上架状态、发布时间、更新时间 |
| `covers.*` | 封面在本目录内的相对路径与原始 URL |
| `site_url` | 对应站点播放页，便于人工核对 |

### episodes.csv

一行一集，含 `is_free`、`fee`、`unlock_type`、`duration_sec`、`hls_480/720`，
可以直接作为「分集表」的导入源。HLS 地址带站点签名（短时有效），
正式环境需要替换为自家 Bunny Stream 地址。

## 5. 数据质量与边界

- 封面为站点原图，竖版约 1125×1878（3:5），横版缩略图用于播放页背景。
- 站点 `rating` 全为 0，没有真实评分；本数据用播放量 / 点赞 / 收藏 / 分类排名表达热度。
- 年龄分级与地区站点没有下发，字段保留为空并在 `*_note` 里写明，避免把编造值混进真实数据。
- 视频正片未下载（体积大且带短时签名），只保留 HLS 路径结构用于对接 Bunny Stream。
- 抓取为公开接口的常规读取，频率已限制在每秒数条，脚本可重复执行。

## 6. 分类覆盖

{top}

完整覆盖见 `manifest.json` 的 `category_coverage` 与 `genre_page_tag_coverage`。

## 7. 重新生成

```bash
cd {out_dir.parents[1] if len(out_dir.parents) > 1 else '.'}
.venv/bin/python scripts/fetch_litetv_catalog.py all --target {manifest['requested_count']}
.venv/bin/python scripts/fetch_litetv_catalog.py export   # 仅用缓存重新导出，不联网
```
"""
    (out_dir / "README.md").write_text(readme)


# --------------------------------------------------------------------------- #
# 阶段 8：产物自检
# --------------------------------------------------------------------------- #
def jpeg_size(path: Path):
    data = path.read_bytes()[:65536]
    if not data.startswith(b"\xff\xd8"):
        return None
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3):
            height = int.from_bytes(data[i + 5:i + 7], "big")
            width = int.from_bytes(data[i + 7:i + 9], "big")
            return width, height
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        segment = int.from_bytes(data[i + 2:i + 4], "big")
        i += 2 + segment
    return None


def verify_outputs(out_dir: Path) -> int:
    print(f"\n自检 {out_dir}")
    problems: list[str] = []

    manifest = json.loads((out_dir / "manifest.json").read_text())
    items = json.loads((out_dir / "dramas.json").read_text())["items"]
    print(f"  dramas.json      {len(items)} 部（manifest 记 {manifest['delivered_count']} 部）")
    if len(items) != manifest["delivered_count"]:
        problems.append("dramas.json 数量与 manifest 不一致")
    if len(items) < manifest["requested_count"]:
        problems.append(f"交付数量 {len(items)} 少于目标 {manifest['requested_count']}")

    ids = [r["media_id"] for r in items]
    if len(set(ids)) != len(ids):
        problems.append("存在重复 media_id")

    for label, bad in (
        ("缺少有效简介", [r["media_id"] for r in items if len(r["description"] or "") < 40]),
        ("缺少分类", [r["media_id"] for r in items if not r["classification"]["main_category"]]),
        ("没有分集记录", [r["media_id"] for r in items if not r["episodes"]["items"]]),
    ):
        if bad:
            problems.append(f"{len(bad)} 部{label}: {bad[:5]}")

    ep_total = sum(len(r["episodes"]["items"]) for r in items)
    ep_dur = sum(1 for r in items for e in r["episodes"]["items"] if e["duration_sec"])
    print(f"  分集              {ep_total} 集，带时长 {ep_dur} 集 ({ep_dur / max(1, ep_total):.0%})")

    cover_problems, dims = [], Counter()
    for r in items:
        for kind in ("poster", "thumb"):
            rel = r["covers"][f"{kind}_file"]
            if not rel:
                cover_problems.append(f"{r['media_id']} 缺 {kind}")
                continue
            fp = out_dir / rel
            if not fp.exists():
                cover_problems.append(f"{rel} 不存在")
                continue
            size = jpeg_size(fp)
            if not size:
                cover_problems.append(f"{rel} 不是有效 JPEG")
            else:
                dims[f"{kind} {size[0]}x{size[1]}"] += 1
    if cover_problems:
        problems.append(f"封面问题 {len(cover_problems)} 处: {cover_problems[:5]}")
    print(f"  封面              尺寸分布 {dict(dims)}")

    subs = list((out_dir / "subtitles").glob("*.srt")) if (out_dir / "subtitles").exists() else []
    print(f"  字幕              {len(subs)} 个")
    if len(subs) < len(items) * 0.8:
        problems.append(f"字幕只有 {len(subs)} 个，明显少于剧集数")

    with (out_dir / "episodes.csv").open(encoding="utf-8-sig") as fh:
        ep_rows = sum(1 for _ in csv.DictReader(fh))
    print(f"  episodes.csv      {ep_rows} 行")
    if ep_rows != ep_total:
        problems.append(f"episodes.csv {ep_rows} 行与分集总数 {ep_total} 不一致")

    with (out_dir / "dramas.csv").open(encoding="utf-8-sig") as fh:
        drama_rows = sum(1 for _ in csv.DictReader(fh))
    if drama_rows != len(items):
        problems.append(f"dramas.csv {drama_rows} 行与剧集数 {len(items)} 不一致")

    index_rows = json.loads((out_dir / "catalog_index.json").read_text())["items"]
    print(f"  catalog_index     {len(index_rows)} 条")

    for name in ("categories.json", "tags.json"):
        try:
            json.loads((out_dir / name).read_text())
        except json.JSONDecodeError as exc:
            problems.append(f"{name} 解析失败: {exc}")

    raw_files = list((out_dir / "raw").glob("*.json")) if (out_dir / "raw").exists() else []
    if len(raw_files) != len(items):
        problems.append(f"raw/ 有 {len(raw_files)} 个文件，应为 {len(items)}")

    size_mb = sum(p.stat().st_size for p in out_dir.rglob("*") if p.is_file()) / 1024 / 1024
    print(f"  体积              {size_mb:.1f} MB")

    if problems:
        print("\n  [FAIL] 发现问题:")
        for p in problems:
            print(f"    - {p}")
        return 1
    print("\n  [OK] 全部检查通过")
    return 0


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def cache_digest(cache_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(cache_dir.rglob("*.json")):
        digest.update(path.name.encode())
        digest.update(str(path.stat().st_size).encode())
    return digest.hexdigest()[:16]


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取 lite.tv 内容准备数据")
    parser.add_argument("stage", nargs="?", default="all",
                        choices=["all", "taxonomy", "enumerate", "select", "details", "media", "export", "verify"])
    parser.add_argument("--target", type=int, default=100, help="目标剧集数量")
    parser.add_argument("--max-id", type=int, default=900, help="枚举 media_id 上限")
    parser.add_argument("--min-episodes", type=int, default=20, help="正片最小集数")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "data" / ".cache")
    parser.add_argument("--out-dir", type=Path,
                        default=ROOT / "data" / f"litetv-catalog-{dt.date.today():%Y%m%d}")
    args = parser.parse_args()

    client = LiteTVClient(args.cache_dir)
    out_dir = args.out_dir
    if args.stage != "export":
        print("访客登录 lite.tv ...")
        client.login()
        print("  登录成功")

    ctx: dict = {"taxonomy": {}, "selected": [], "selected_ids": [], "slides": {}, "slides_records": []}
    selection_file = args.cache_dir / "selection.json"

    try:
        if args.stage in ("all", "taxonomy"):
            print("[1/7] 分类字典与站点榜单")
            stage_taxonomy(client, ctx)

        if args.stage in ("all", "enumerate"):
            print("[2/7] 枚举 media_id 空间")
            stage_enumerate(client, ctx, args.max_id, args.workers)

        if args.stage in ("all", "select"):
            print("[3/7] 挑选剧集并保证分类覆盖")
            cached_selection = None
            if selection_file.exists():
                cached_selection = json.loads(selection_file.read_text())
            if cached_selection and cached_selection.get("target") == args.target:
                ctx["selected_ids"] = cached_selection["ids"]
                by_id = {}
                for path in ctx["selected_ids"]:
                    fp = args.cache_dir / f"media_info/{path}.json"
                    by_id[path] = (json.loads(fp.read_text()).get("data") or {})
                ctx["selected"] = [by_id[i] for i in ctx["selected_ids"]]
                print(f"  复用已保存的选片结果 {len(ctx['selected'])} 部")
            else:
                if not ctx["taxonomy"]:
                    stage_taxonomy(client, ctx)
                stage_select(client, ctx, args.target, args.min_episodes)
                selection_file.write_text(json.dumps(
                    {"target": args.target, "ids": ctx["selected_ids"], "at": dt.datetime.now().isoformat()},
                    ensure_ascii=False, indent=1))

        if not ctx["selected"] and selection_file.exists():
            ctx["selected_ids"] = json.loads(selection_file.read_text())["ids"]
            ctx["selected"] = [
                (json.loads((args.cache_dir / f"media_info/{mid}.json").read_text()).get("data") or {})
                for mid in ctx["selected_ids"]
            ]

        if args.stage in ("all", "details"):
            print("[4/7] 分集、播放与解锁明细")
            stage_details(client, ctx, args.workers)

        if args.stage in ("all", "export"):
            print("[5/7] 整理结构化交付数据")
            stage_export(client, ctx, out_dir)

        if args.stage in ("all", "media"):
            print("[6/7] 下载封面与字幕")
            stage_media(client, ctx, out_dir, args.workers)
            # 媒体文件落盘后重建一次记录，让 manifest 里的文件数量准确
            if args.stage == "all":
                stage_export(client, ctx, out_dir)

        if args.stage in ("all", "export"):
            print("[7/7] 写出 manifest / CSV / README")
            write_outputs(client, ctx, out_dir, args.target)
            manifest = json.loads((out_dir / "manifest.json").read_text())
            write_readme(out_dir, manifest)
            print(f"\n完成：{out_dir}")
            print(f"  {manifest['counts']}")

        if args.stage == "verify":
            return verify_outputs(out_dir)
    except LiteTVError as exc:
        print(f"抓取失败: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
