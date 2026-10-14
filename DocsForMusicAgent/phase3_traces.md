# 阶段 3 典型 trace（成功 5 / 边界 5）

## 成功样例

### `Move a Little Closer`
- 回复：为你找到以下歌曲：
- `7359` Move a Little Closer — Steven Dunston [平静/慵懒/治愈/浪漫/温暖/恋爱/睡前]
- `2243031` Move Your Body — ChoiceTune [史诗/燃/健身/派对]
- `1831809` Lover — Square a Saw [开心/浪漫/温暖/甜美/恋爱/派对/深夜]
- `889548` The little voice — Laurent Danis [伤感/平静/怀旧/治愈/深夜/睡前]
- `1969660` Pyre (feat.Happy Little Accidents,Jade Gritty & Aurc) — Tab [平静/忧郁/梦幻/咖啡馆/深夜/睡前]
- 流水线：intent → recall → rank(24) → respond(5)

### `有句歌词大概是说 move a little closer`
- 回复：为你找到以下歌曲：
- `7359` Move a Little Closer — Steven Dunston [平静/慵懒/治愈/浪漫/温暖/恋爱/睡前]
- `1336999` Gravity — eve [伤感/平静/忧郁/浪漫/失恋/恋爱/深夜]
- `1892677` Simplement — Ben5bass [平静/怀旧/浪漫/温暖/咖啡馆/恋爱/散步]
- `110207` Caught Inside A Dream — Briareus [平静/慵懒/梦幻/浪漫/恋爱/深夜/睡前]
- `1658149` Show Me — AIRE [伤感/励志/希望/忧郁/燃/学习/深夜]
- 流水线：intent → recall → rank(30) → respond(5)

### `有时候没有你我简直无法呼吸`
- 回复：为你找到以下歌曲：
- `1336999` Gravity — eve [伤感/平静/忧郁/浪漫/失恋/恋爱/深夜]
- `1831809` Lover — Square a Saw [开心/浪漫/温暖/甜美/恋爱/派对/深夜]
- `1926466` WHEN I'M WITHOUT YOU — Egor Budennyy [伤感/史诗/失落/忧郁/浪漫/失恋/恋爱/深夜/雨天]
- `1430843` I Need To Go — Sam Garbett [伤感/失落/孤独/燃/失恋/旅行/深夜]
- `1841895` Vivere per te — Ben5bass [伤感/希望/愤怒/浪漫/燃/失恋/恋爱/深夜]
- 流水线：intent → recall → rank(30) → respond(5)

### `求一首深夜伤感的德语民谣`
- 回复：（放宽了：去掉软偏好、放宽场景）为你找到以下歌曲：（放宽：['去掉软偏好', '放宽场景']）
- `1334445` 777 — Sperling [伤感/希望/开心/治愈/燃/失恋/深夜]
- 流水线：intent → recall → rank(1) → relax(1) → relax(1) → respond(1)

### `推荐点适合跑步的燃曲`
- 回复：为你找到以下歌曲：
- `1667885` Future of the Sound — J.O.R.B.I [励志/开心/燃/健身/派对/跑步]
- `137699` Doira — Runa Raido [燃/神秘/开车/跑步]
- `1480423` The Path To Power — Paul Werner [史诗/燃/健身/跑步]
- `1953928` The Final Battle (With Choir) — Grégoire Lourme [史诗/愤怒/燃/紧张/健身/跑步]
- `2056435` Motivation Uplifting Rock — ZaharVips [励志/史诗/燃/健身/跑步]
- 流水线：intent → recall → rank(20) → respond(5)
## 边界样例

### `Coldplay 的 Yellow`
- 回复：曲库里没有 Coldplay 的《Yellow》。另有一首同名《Yellow》（歌手：Sam Opoku）。
- 流水线：intent → recall

### `求一首叫 Night Str 的歌，Sergey Gulevich 唱的`
- 回复：为你找到以下歌曲：
- `1693791` Night Star — Sergey Gulevich [平静/梦幻/浪漫/恋爱/深夜/睡前]
- `1818772` Feel the Day — Sergey Gulevich [平静/梦幻/治愈/清晨/睡前]
- `2301582` Spheroid — Youthopian [平静/梦幻/神秘/学习/深夜/睡前]
- `709208` Réflexions — Francesco [伤感/平静/忧郁/怀旧/梦幻/学习/深夜/睡前]
- `1401508` Stimulate - One Word — Gelbriseok [伤感/失落/孤独/平静/忧郁/慵懒/失恋/深夜]
- 流水线：intent → recall → rank(20) → respond(5)

### `来首周杰伦的晴天`
- 回复：曲库里没有「晴天」这首歌，要不要试试按风格或氛围描述来找？
- 流水线：intent → recall

### `Someone Like You`
- 回复：曲库里没有「Someone Like You」这首歌，要不要试试按风格或氛围描述来找？
- 流水线：intent → recall

### `纯音乐、史诗、睡前的歌`
- 回复：（放宽了：去掉软偏好、放宽场景）为你找到以下歌曲：（放宽：['去掉软偏好', '放宽场景']）
- `2290107` Emotional Experimental Cinematic — Sevennotes [史诗/平静/梦幻/神秘/学习/工作/深夜]
- `2273466` 5 - NOSTALGIE OCÉANE — Thierry TREMELET [史诗/平静/怀旧/梦幻/咖啡馆/深夜/睡前]
- `1080606` Die Ballade vom Uhrwerk — FlauschGAU [史诗/神秘/紧张/学习/深夜]
- `2283948` 8 - AU ROYAUME D'ATLANTIDE — Thierry TREMELET [史诗/梦幻/燃/旅行/深夜]
- `1399380` StrinGIntensitY — Pranal Ingle [励志/史诗/平静/学习/工作/清晨]
- 流水线：intent → recall → rank(1) → relax(1) → relax(10) → respond(5)
