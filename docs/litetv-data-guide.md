# MiniTV 内容数据指南：剧名、封面、视频分别在哪

更新：2026-10-14
适用对象：后端、客户端、管理后台、测试
配套：[lite.tv 正片下载说明](litetv-video-download.md)、[软件架构设计](software-architecture.md)

## 0. 三十秒速查

| 你要找什么 | 去这里 |
|---|---|
| **剧名 / 简介 / 分类 / 集数** | `data/litetv-catalog-20261013/dramas.json`（或 `dramas.csv`） |
| **竖版封面（3:5，海报位）** | `data/litetv-catalog-20261013/covers/<media_id>_poster.jpg`（少数是 `.png`） |
| **横版缩略图（3:4，播放页背景）** | `data/litetv-catalog-20261013/covers/<media_id>_thumb.jpg`（少数是 `.png`） |
| **某集的视频** | `data/litetv-videos-real/<media_id>/<media_id>_ep<集号 3 位>.mp4` |
| **英文字幕** | `data/litetv-catalog-20261013/subtitles/<media_id>_ep<集号>_en.srt` |
| **一站式的对照索引** | `data/litetv-catalog-20261013/index-title-cover-video.csv`（100 行）<br>`data/litetv-catalog-20261013/index-episode-video.csv`（1197 行） |
| **模拟用户（后台演示）** | 见 [第 9 节](#9-本地模拟用户数据后台演示用)：`python -m app.seed_users` |

主键只有两个：**`media_id`（哪部剧）+ `episode_number`（哪一集，从 0 开始）**。
100 部剧在目录数据、视频库、可播清单三处的 `media_id` 完全对齐（差集为空，已核对）。

## 1. 三个目录分别是什么

```
data/
├── litetv-catalog-20261013/     37 MB   414 文件   剧集目录 + 封面 + 字幕 + 索引（站点元数据）
├── litetv-videos-real/          15 GB  1204 文件   站点真片（HLS 转封装 MP4）
└── litetv-official/            3.4 MB    22 文件   站点官方素材（海报 / Banner / 水印）
```

| 目录 | 内容 | 谁生成 |
|---|---|---|
| `litetv-catalog-20261013/` | 100 部剧的标题、简介、分类、集数、解锁规则、金币价、封面、字幕、榜单、会员套餐 | `scripts/fetch_litetv_catalog.py` |
| `litetv-videos-real/` | 1197 集真实视频（100 部剧的免费集），带真实音轨 | `scripts/fetch_litetv_videos.py` |
| `litetv-official/` | 少量官方宣传图（海报、Banner、水印 PNG） | 早前手工归档 |

合成占位视频（`litetv-videos-local` / `-synth`）已于 2026-10-14 全部删除，现在只有真片。

## 2. 命名规则

### 封面

```
data/litetv-catalog-20261013/covers/<media_id>_poster.jpg   竖版，约 1125×1878（3:5），少数 .png
data/litetv-catalog-20261013/covers/<media_id>_thumb.jpg    横版，441×588 / 444×600（3:4）
```

扩展名**不固定**（有 `.jpg` 也有 `.png`），不要手拼路径，读 `dramas.json` 里的
`covers.poster_file` / `covers.thumbnail_file`（索引 CSV 里已经给成可直接用的相对路径）。

### 视频

```
data/litetv-videos-real/<media_id>/<media_id>_ep000.mp4
                                     ^^^^^ 集号，三位补零，从 000 开始
```

例如 `media_id=180` 的第 3 集 → `data/litetv-videos-real/180/180_ep003.mp4`。

### 字幕

```
data/litetv-catalog-20261013/subtitles/<media_id>_ep<集号>_en.srt     97 份，97 部剧各 1 份
```

分布：96 部剧是英文（82 份取自第 0 集、14 份取自第 1 集），1 部剧只有日文字幕。
取的是该剧前几集里第一份带字幕的集，语言优先英文，没有英文时取首个可用语言。
用途是验证字幕链路，**不是全集字幕**。

## 3. 三张对照索引（不想读代码就用它们）

### 3.1 `index-title-cover-video.csv` — 一行一部剧

| 字段 | 含义 |
|---|---|
| `media_id` / `title` | 剧 ID / 英文剧名 |
| `main_category` / `sub_categories` | 主分类 / 细分标签（`|` 分隔） |
| `poster_file` / `thumbnail_file` | 封面相对仓库根的路径 |
| `video_dir` | 该剧视频目录，如 `data/litetv-videos-real/180/` |
| `video_episode_count` | 已有视频的集数 |
| `video_first_file` / `video_last_file` | 第一集 / 最后一集视频的相对路径 |
| `free_episode_min` / `free_episode_max` | 可播（免费）集号范围 |
| `catalog_total_episodes` / `catalog_published_episodes` | 目录数据里的总集数 / 已发布集数 |
| `site_url` | 对应的站点播放页，人工核对用 |
| `description` | 英文简介 |

### 3.2 `index-episode-video.csv` — 一行一集（只有有视频的 1197 行）

`media_id, title, episode_number, video_file, bytes, duration_sec, resolution, poster_file`

这一张表就能回答「这一集到底有没有视频」——**没有出现在表里的集就是没下载的（锁定集）**。

### 3.3 视频库里原有的两个报告

| 文件 | 内容 |
|---|---|
| `data/litetv-videos-real/manifest.json` | 1197 集清单：文件、字节数、标题；`locked` / `failures` 均为空 |
| `data/litetv-videos-real/availability.json` | 每部剧逐集实测出来的 `free_episodes` 与 `locked_from`，**免费范围以此为准** |

## 4. 怎么用

### 按剧名找全部资产（Python）

```python
import csv, pathlib

ROOT = pathlib.Path(".")
index = list(csv.DictReader(open("data/litetv-catalog-20261013/index-title-cover-video.csv", encoding="utf-8-sig")))
row = next(r for r in index if "Mafia Boss" in r["title"])
print(row["title"], "→", row["poster_file"], "|", row["video_episode_count"], "集")

eps = [r for r in csv.DictReader(open("data/litetv-catalog-20261013/index-episode-video.csv", encoding="utf-8-sig"))
       if r["media_id"] == row["media_id"]]
for ep in eps[:3]:
    print(" ", ep["episode_number"], ep["video_file"], ep["duration_sec"], "s", ep["resolution"])
```

### 命令行列出某部剧的全部文件

```bash
MID=180
ls data/litetv-catalog-20261013/covers/${MID}_*
ls data/litetv-videos-real/${MID}/
grep "^${MID}," data/litetv-catalog-20261013/index-episode-video.csv | head
```

### 当作后端种子数据

导入顺序：`categories.json` / `tags.json` → `dramas.csv` → `episodes.csv` →
视频用 `index-episode-video.csv` 关联本地文件或上传后的 URL。
`media_id` 建议直接沿用站点 ID，方便和这里的索引对齐。

如果剧集已经写进数据库，但后台「分集」页为空，执行幂等导入器补齐目录中的全部
分集元数据（已有分集不会被覆盖）：

```bash
cd backend
../.venv/bin/python -m app.seed_catalog_episodes              # 全部精确匹配的剧
../.venv/bin/python -m app.seed_catalog_episodes --drama-id 15
../.venv/bin/python -m app.seed_catalog_episodes --dry-run
```

把某部剧的本地 MP4 接到 Bunny Stream 并回填 `bunny_video_id`：

```bash
cd backend
../.venv/bin/python -m app.import_local_videos --drama-id 15 --dry-run
../.venv/bin/python -m app.import_local_videos --drama-id 15 --workers 3
../.venv/bin/python -m app.import_local_videos --drama-id 15 --sync-status
```

已有 Bunny GUID 的分集会自动跳过，失败后可以重复执行；后台分集表的「预览」使用
服务端签发的 Bunny Player 临时链接，不会把未发布分集直接暴露给客户端。

清理「登记了但从未上传」的占位分集（`seed_catalog_episodes` / 旧版「登记 64 集」
留下的 `not_uploaded` 行；后台现在可以在「分集」页手动「上传新分集」逐个补）：

```bash
cd backend
../.venv/bin/python -m app.prune_not_uploaded_episodes --dry-run
../.venv/bin/python -m app.prune_not_uploaded_episodes
../.venv/bin/python -m app.prune_not_uploaded_episodes --drama-id 1
```

只删除 `status=not_uploaded` 且没有 Bunny 视频 ID 的行，连同其上传会话 / 播放打点 /
CDN 记录一起删除；被解锁记录、金币流水、评论引用的行会跳过并在输出中列出，
每次删除写入 `audit_logs`（action=`episode_prune_not_uploaded`）。

## 5. 100 部剧总表

`media_id` → 剧名 → 封面文件名 → 视频目录 → 有视频的集数 → 免费集范围：

| media_id | 剧名 | 主分类 | 封面 | 视频目录 | 有视频集数 | 免费集范围 |
|---|---|---|---|---|---|---|
| 14 | After Divorced I Took Over The Wealthy Family | Revenge | `14_poster.jpg` | `14/` | 80 | 0–79 |
| 15 | Timeless Love | Time Travel | `15_poster.jpg` | `15/` | 80 | 0–79 |
| 18 | Forbidden Desires: My Alpha's Fate | Fantasy | `18_poster.jpg` | `18/` | 80 | 0–79 |
| 24 | Marry My Husband, Please! | Romance | `24_poster.jpg` | `24/` | 7 | 0–6 |
| 26 | Forbidden Romance with Mafia | Romance | `26_poster.jpg` | `26/` | 7 | 0–6 |
| 32 | Daddy, This Is My Mommy | Romance | `32_poster.jpg` | `32/` | 7 | 0–6 |
| 36 | Mated to My Cursed Alpha | Romance | `36_poster.jpg` | `36/` | 10 | 0–9 |
| 37 | Hiding Her Identity: The Poor Wife Wants Revenge | Revenge | `37_poster.jpg` | `37/` | 7 | 0–6 |
| 38 | My Lover My Grave | Drama | `38_poster.jpg` | `38/` | 7 | 0–6 |
| 40 | The CEO of Flash Marriage Adores Me | Drama | `40_poster.jpg` | `40/` | 7 | 0–6 |
| 41 | Elite Love Triangle: Three Magnates in Pursuit | Drama | `41_poster.jpg` | `41/` | 7 | 0–6 |
| 42 | Mating of the Twilight Goddess | Romance | `42_poster.jpg` | `42/` | 7 | 0–6 |
| 43 | Mr. Gu, Please Behave! | Tearjerker | `43_poster.jpg` | `43/` | 7 | 0–6 |
| 44 | The Grand Fortune Teller | Fantasy | `44_poster.jpg` | `44/` | 7 | 0–6 |
| 45 | Kill me, heal me | Thriller | `45_poster.jpg` | `45/` | 7 | 0–6 |
| 46 | My "Gigolo" Alpha | Werewolf | `46_poster.jpg` | `46/` | 7 | 0–6 |
| 47 | Atlante’s Mistaken Fiancée | Romance | `47_poster.jpg` | `47/` | 15 | 0–14 |
| 48 | The Dual Nature of His Seductive Wife | Revenge | `48_poster.jpg` | `48/` | 7 | 0–6 |
| 49 | To Avenge My Ex, I Married a Stranger | Rom-Com | `49_poster.jpg` | `49/` | 7 | 0–6 |
| 50 | Freeze! My Wanted Lover | Revenge | `50_poster.jpg` | `50/` | 14 | 0–13 |
| 51 | Baby Surprise: Daddy's a Boss | Drama | `51_poster.jpg` | `51/` | 7 | 0–6 |
| 52 | After Divorce I Become Hybrid Alpha's Luna | Werewolf | `52_poster.jpg` | `52/` | 11 | 0–10 |
| 53 | Lost and Found | Tearjerker | `53_poster.jpg` | `53/` | 7 | 0–6 |
| 54 | Marrying My Ex-Husband's Boss | Drama | `54_poster.jpg` | `54/` | 7 | 0–6 |
| 56 | Quick Marriage: Please Don't Fall Too Hard | Rom-Com | `56_poster.jpg` | `56/` | 7 | 0–6 |
| 57 | Wife's Revenge | Drama | `57_poster.jpg` | `57/` | 7 | 0–6 |
| 58 | Driven to Love | Rom-Com | `58_poster.jpg` | `58/` | 9 | 0–8 |
| 60 | I Accidentally Married A Billionaire | Romance | `60_poster.jpg` | `60/` | 7 | 0–6 |
| 62 | Spoiled By The Billionaire | Romance | `62_poster.jpg` | `62/` | 12 | 0–11 |
| 63 | The Haughty Alpha’s Fateful Mate | Werewolf | `63_poster.jpg` | `63/` | 7 | 0–6 |
| 66 | Heiress in Distress: The Comeback | Romance | `66_poster.jpg` | `66/` | 7 | 0–6 |
| 68 | But Daddy I Love Him | Romance | `68_poster.jpg` | `68/` | 12 | 0–11 |
| 69 | From Contract Lover to the Billionaire's Bride | Tearjerker | `69_poster.jpg` | `69/` | 17 | 0–16 |
| 70 | Madam, The CEO is Doing Odd Jobs Again | Rom-Com | `70_poster.jpg` | `70/` | 11 | 0–10 |
| 76 | Reborn: The Legend Luna Awakening | Werewolf | `76_poster.jpg` | `76/` | 7 | 0–6 |
| 78 | Crimson Eye of Destiny | Fantasy | `78_poster.jpg` | `78/` | 13 | 0–12 |
| 79 | Legendary Stock God | Tycoon | `79_poster.jpg` | `79/` | 10 | 0–9 |
| 80 | Nine Dragons War God | Super Power | `80_poster.jpg` | `80/` | 11 | 0–10 |
| 83 | Return of the Supreme Lord | Super Power | `83_poster.jpg` | `83/` | 11 | 0–10 |
| 89 | Never Enjoyed Divorce Like This | Romance | `89_poster.jpg` | `89/` | 9 | 0–8 |
| 92 | Mr. Vampire Wants Me Everyday | Vampire | `92_poster.jpg` | `92/` | 10 | 0–9 |
| 93 | Falling For My Estranged Wife | Rom-Com | `93_poster.jpg` | `93/` | 10 | 0–9 |
| 94 | Your Ladyship's Escorting CEO | Romance | `94_poster.jpg` | `94/` | 9 | 0–8 |
| 97 | Ultimate Dragon King | Super Power | `97_poster.jpg` | `97/` | 10 | 0–9 |
| 100 | Bonded to Love: My Pretend Boyfriend | Romance | `100_poster.jpg` | `100/` | 7 | 0–6 |
| 101 | Champion, She's Not The One | Romance | `101_poster.jpg` | `101/` | 12 | 0–11 |
| 102 | My Mafia Life: From Maid to Bride | Romance | `102_poster.jpg` | `102/` | 10 | 0–9 |
| 105 | Regretful Ex-Husband After My Heartbreak | Drama | `105_poster.jpg` | `105/` | 14 | 0–13 |
| 108 | I Became My Ex-Husbands Boss | Revenge | `108_poster.jpg` | `108/` | 8 | 0–7 |
| 111 | Flash Vows | Romance | `111_poster.jpg` | `111/` | 11 | 0–10 |
| 113 | After Divorce, I Owned Three Billionaires | Romance | `113_poster.jpg` | `113/` | 10 | 0–9 |
| 114 | My Wife is My Divorce Lawyer | Romance | `114_poster.jpg` | `114/` | 13 | 0–12 |
| 118 | Hopeless Miss Mafia | Revenge | `118_poster.jpg` | `118/` | 10 | 0–9 |
| 119 | Light My Fire | Tearjerker | `119_poster.jpg` | `119/` | 8 | 0–7 |
| 123 | Disowned Dad Is the Billionaire | Family | `123_poster.jpg` | `123/` | 14 | 0–13 |
| 127 | My Kept Man is a Secret Billionaire | Romance | `127_poster.jpg` | `127/` | 13 | 0–12 |
| 128 | Got Myself A Priceless Husband | Rom-Com | `128_poster.jpg` | `128/` | 8 | 0–7 |
| 129 | Rebirth of the Forgotten Heir | Revenge | `129_poster.jpg` | `129/` | 14 | 0–13 |
| 131 | The Long Way Home | Reunion | `131_poster.jpg` | `131/` | 12 | 0–11 |
| 132 | The Office Pushover Is The Real Heiress | Romance | `132_poster.jpg` | `132/` | 12 | 0–11 |
| 133 | The Joys and Sorrows of Life | Reunion | `133_poster.jpg` | `133/` | 9 | 0–8 |
| 134 | Flash Marriage At 40 My Second Chance | Romance | `134_poster.jpg` | `134/` | 9 | 0–8 |
| 137 | Phoenix's Revenge | Tearjerker | `137_poster.jpg` | `137/` | 11 | 0–10 |
| 138 | Incurable Love | Werewolf | `138_poster.jpg` | `138/` | 9 | 0–8 |
| 140 | Vampire's Remedy | Vampire | `140_poster.jpg` | `140/` | 7 | 0–6 |
| 141 | Spoiled By My Billionaire Sugar Daddy | Romance | `141_poster.jpg` | `141/` | 7 | 0–6 |
| 142 | See you again: CEO's sweet trap | Romance | `142_poster.jpg` | `142/` | 8 | 0–7 |
| 143 | Santa Sent Me A Billionaire Husband | Romance | `143_poster.jpg` | `143/` | 9 | 0–8 |
| 148 | Stolen Son's Triumphant Return | Reunion | `148_poster.jpg` | `148/` | 12 | 0–11 |
| 150 | Black Myth: Qilin Awakening | Fantasy | `150_poster.jpg` | `150/` | 16 | 0–15 |
| 153 | Under False Face | Thriller | `153_poster.jpg` | `153/` | 13 | 0–12 |
| 158 | From Lost Heiress to Royal Highness | Romance | `158_poster.jpg` | `158/` | 9 | 0–8 |
| 163 | Ops! I Married With My Forgetful Ex-boyfriend | Tearjerker | `163_poster.jpg` | `163/` | 9 | 0–8 |
| 165 | Breaking Into the CEO's Secret Playroom | Romance | `165_poster.jpg` | `165/` | 11 | 0–10 |
| 167 | Reborn as Heir's Dreaming Girl (Japanese-dubbed) | Romance | `167_poster.jpg` | `167/` | 11 | 0–10 |
| 168 | Unspeakable love | Tearjerker | `168_poster.jpg` | `168/` | 7 | 0–6 |
| 169 | Don't Forget Me Again, Alpha! | Werewolf | `169_poster.jpg` | `169/` | 8 | 0–7 |
| 170 | Forbidden Affair With My Husband | Romance | `170_poster.jpg` | `170/` | 11 | 0–10 |
| 174 | Love Trap with My Dashing Knight | Romance | `174_poster.jpg` | `174/` | 10 | 0–9 |
| 175 | Love You Beyond Six Months | Romance | `175_poster.jpg` | `175/` | 10 | 0–9 |
| 176 | Royal Heir Breaks My Heart In A Warzone | Romance | `176_poster.jpg` | `176/` | 10 | 0–9 |
| 178 | Cohabitating Lover | Romance | `178_poster.jpg` | `178/` | 10 | 0–9 |
| 179 | Forced to Love the Ruthless CEO | Romance | `179_poster.jpg` | `179/` | 10 | 0–9 |
| 180 | 30 Days Swapping Lives with a Mafia Boss | Rom-Com | `180_poster.jpg` | `180/` | 9 | 0–8 |
| 182 | Memory Reset: Mr. Restraint Begged for Love | Romance | `182_poster.jpg` | `182/` | 12 | 0–11 |
| 183 | The Secret Recipe to Snatch a Billionaire | Romance | `183_poster.jpg` | `183/` | 9 | 0–8 |
| 184 | Barefoot Meets Billionaire | Rom-Com | `184_poster.jpg` | `184/` | 11 | 0–10 |
| 185 | From Lost Heiress to Mafia Queen | Romance | `185_poster.jpg` | `185/` | 8 | 0–7 |
| 186 | Mancini's Forbidden Bride | Romance | `186_poster.jpg` | `186/` | 11 | 0–10 |
| 187 | My Bodyguard Husband Is the Boss | Rom-Com | `187_poster.jpg` | `187/` | 13 | 0–12 |
| 198 | The Fated Encounter, Love in Disguise S1 | Rom-Com | `198_poster.jpg` | `198/` | 14 | 0–13 |
| 199 | In Love With My Ex's Uncle | Revenge | `199_poster.jpg` | `199/` | 11 | 0–10 |
| 204 | I Killed My Stepfather with A Kiss | Forbidden Love | `204_poster.jpg` | `204/` | 13 | 0–12 |
| 205 | Why Rich Woman Kill | Revenge | `205_poster.jpg` | `205/` | 12 | 0–11 |
| 210 | Fated to Miss Her | Bitter Love | `210_poster.jpg` | `210/` | 12 | 0–11 |
| 211 | The Intern is the Undercover Boss | Billionaire/CEO | `211_poster.jpg` | `211/` | 11 | 0–10 |
| 212 | My Billion Dollar Waiter | Love After Marriage | `212_poster.jpg` | `212/` | 10 | 0–9 |
| 213 | Ms. Billionaire's Love Business | Love After Marriage | `213_poster.jpg` | `213/` | 8 | 0–7 |
| 736 | My Wife's Fake Amnesia | Revenge | `736_poster.jpg` | `736/` | 11 | 0–10 |
| 758 | Apron Down, Hornbook Up | Drama | `758_poster.jpg` | `758/` | 15 | 0–14 |

## 6. 覆盖口径（重要）

| 数据 | 覆盖 |
|---|---|
| 封面 | **100 部全覆盖**（每部竖版 1 张 + 横版 1 张，共 200 张） |
| 视频 | **只有免费集**：1197 集，每部 7–80 集 |
| 目录元数据 | 全部 6964 集都有记录（集号、是否免费、金币价、HLS 结构） |
| 字幕 | 97 份样例（不是全集字幕） |

所以：**「目录里有这一集」不等于「有这一集的视频」**。
判断某集有没有视频，只有两个可靠来源：

1. `index-episode-video.csv` 里有没有这一行；
2. `availability.json` 里该剧的 `free_episodes` 包含不包含这个集号。

锁定集（站点返回 `50003 Please unlock in order`）没有下载，也没有做任何绕过付费墙的尝试。

### 6.1 三部剧的集数例外

| media_id | 剧名 | 目录声明总集数 | 实际可播/已下载 |
|---|---|---|---|
| 14 | After Divorced I Took Over The Wealthy Family | 59 | **80** |
| 15 | Timeless Love | 72 | **80** |
| 18 | Forbidden Desires: My Alpha's Fate | 60 | **80** |

这三部剧站点自己的元数据就自相矛盾（`media_info` 说 59 集，`play` 接口说 55 集，
实际逐集试到第 79 集都能播）。**一律以 `availability.json` / 下载结果为准。**

## 7. 口径陷阱（踩过的坑）

1. **`is_free` 是反的**：站点接口里 `is_free=1` 表示「需要解锁」，`0` 才是免费集。
   `dramas.json` 已经换算成直觉正确的 `is_free`，原始值保留在 `raw_is_free`。
2. **免费范围不要用 `is_free` 推**：接口的 `is_free` 和真实可播范围对不上
   （目录标 0–7 集，实测常常是 0–8 集）。以 `availability.json` 为准。
3. **重复内容不等于文件损坏**：1197 个文件里有 254 个是硬链接（943 份不同内容，
   见 `dedupe.json`），所以会出现「两集 `bytes` 完全相同」的情况，这是正常的省盘处理。
4. **清晰度是混合的**：1059 集是 480×854，138 集是 720×1280（`180`、`183` 的 0–8 集，以及 2026-10-14 从 480p 升级到 720p 的 120 集；详见 `p720-upgrade-20261014.json`）。
5. **封面是真实海报、视频是站点正片**，仅限本地开发/联调使用，不要随产品对外发布；
   像素级设计对比继续用 `assets/titled` 那套图，不要换成这里的素材。
6. **别再建合成占位目录**：`scripts/build_local_video_library.py` 已废弃并默认拒跑。

## 8. 索引怎么重新生成

```bash
.venv/bin/python scripts/build_data_index.py              # 重新探测时长/分辨率并刷新两张 CSV
.venv/bin/python scripts/build_data_index.py --no-probe   # 跳过探测，几秒完成
```

数据本身（目录 / 视频）的重新生成方式见
[litetv-video-download.md](litetv-video-download.md) 与
[fetch_litetv_catalog.py](../scripts/fetch_litetv_catalog.py)。

## 9. 本地模拟用户数据（后台演示用）

第 1–8 节讲的是「内容」数据（剧、封面、视频）。**用户**数据在数据库里，用种子脚本生成：

```bash
cd backend && ../.venv/bin/python -m app.seed_users              # 建 20 个模拟用户（缺谁补谁）
cd backend && ../.venv/bin/python -m app.seed_users --reset      # 先删掉这 20 个再重建
cd backend && ../.venv/bin/python -m app.seed_users --count 40   # 建更多
```

| 项目 | 说明 |
|---|---|
| 脚本 | `backend/app/seed_users.py`（`python -m app.seed_users`） |
| 账号 | 20 个 `@example.com` 用户，英文昵称 + 10 位 `public_id`，注册时间散布在最近 90 天 |
| 连带数据 | 每人一个钱包 + 金币流水（共 144 行）、7 个会员（各 1 条 active 订阅 + 1 笔已支付订单）、部分第三方绑定 |
| 不变量 | `wallet_accounts.balance == sum(coin_ledger.amount)`；每个 `member` 都有 active 订阅和 paid 订单 |
| 幂等 | 按邮箱判断，重复执行只补缺失的行；`--reset` 只删这些 `@example.com` 账号，不动真实数据 |
| 确定 | 固定随机种子，同样参数每次生成同样的名字 / 金币 / 金额，方便截图对比 |
| 看效果 | 后台 `http://127.0.0.1:5173/users`（用户管理）；底部「调整金币」可直接给模拟用户加减金币 |
| 测试 | `backend/tests/test_seed_users.py`（10 项：幂等、确定性、钱包=流水、会员一致性、重置隔离、调账联调） |

模拟用户只有数据、没有密码（登录是邮箱验证码流），不能直接登录客户端；它们用于后台用户管理、
数据看板、财务订单页的演示与联调。

## 10. 数据看板的口径（真实事件，非手写数字）

`/admin/v1/dashboard` 的每个数字都由产品自己写入的表聚合而来，`daily_metrics` 只是读取时的
快照缓存（每次打开看板重算并 upsert）：

| 指标 | 来源 |
|---|---|
| 新增用户 | `users.created_at` |
| 活跃用户 | 当天有播放事件的**登录用户去重**（`playback_events`） |
| 播放次数 / 完播次数 | `playback_events.kind = start / complete` |
| 完播率 | 完播次数 / 播放次数（万分比，UI 显示为百分比） |
| 付费订单 / 营收 | `orders` 中 `status='paid'` 的 `paid_at`（按洛杉矶业务日切分） |
| 金币消耗 | `coin_ledger` 当天负数流水绝对值 |
| 解锁 | `entitlements.created_at` 当天条数 |
| 累计/待办 | 总用户、有效会员、已发布剧集与集数、待发布集数、待审评论、累计营收、累计播放 |

客户端打点链路：`app/lib/screens/player_screen.dart` 在播放成功后 POST `/v1/playback/start`，
每 15 秒 POST `/v1/playback/progress`，接近片尾 POST `/v1/playback/complete`；`start` 事件同时
自增 `dramas.play_count`（后台剧集列表的「播放量」）。

本地想看到非零看板（走真实公开 API，不直写数据库）：

```bash
cd backend && ../.venv/bin/python - <<'PY'
import os, sys, redis, httpx
sys.path.insert(0, ".")
from app.db import load_env; load_env()          # 读 .env 里的 REDIS_URL
uid, token, episode_id = 1, "guide-demo", 1
redis.Redis.from_url(os.environ["REDIS_URL"]).set(f"minitv_user_session:{token}", uid, ex=600)
http = httpx.Client(trust_env=False, timeout=10, headers={"Authorization": f"Bearer {token}"})
for kind in ("start", "progress", "complete"):
    http.post(f"http://127.0.0.1:18110/v1/playback/{kind}", json={"episode_id": episode_id, "position_seconds": 0})
print("ok")
PY
```

跑完刷新看板：当天播放 +1、完播 +1、活跃用户包含该 uid；剧集列表「播放量」+1。
