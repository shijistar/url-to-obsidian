# Netease (163.com) Placeholder & Video-Link Quirks

## `news/v/` video short links → anti-scrape placeholder
- URL form: `https://c.m.163.com/news/v/<ID>.html` (often with `?spss/spsnuid/spsvid/spstoken` trackers)
- Extractor (`cli.mjs --no-browser`, static) returns:
  - `ok:true` but `title` present, `author:''`, `published:''`
  - `markdown` starts with `<video src="" controls=""></video>\n\n网络不给力\n\n重新加载` then title + `声明：个人原创` + a list of UNRELATED search-recommendation links (普京…, 开学诈骗…, etc.)
  - `wordCount` ≈ 123, `method:static`
- Root cause: content is a SHORT VIDEO; source returns placeholder + recommendations, no article body.
- Action: abort clip; ask user. Contrast: `news/a/<ID>.html` article links clip cleanly (author/date/1500+ words).

## `empty.png` placeholder image — root cause & extractor fix
- **Where the placeholder comes from (root cause)**: Netease lazy-loads images. The raw DOM is
  `<img class="js-preview-img" src="https://static.ws.126.net/163/frontend/images/2022/empty.png" data-echo="http://dingyue.ws.126.net/2026/.../REAL.jpg">`.
  `src` is a 1×1 transparent pixel shown immediately; the real URL lives in `data-echo` (also `data-src`/`data-original` on other sites). Front-end JS swaps `data-echo`→`src` only when the image scrolls into view.
- **Bug (pre-fix extractor)**: `extractor/src/extractor.mjs` `absolutizeLinks()` only read `img[src]`, so clipped markdown always contained `empty.png` and the REAL figure URL was lost.
- **Fix (shipped in `fix/extractor-lazy-image`, commit `a327697`)**: added `extractLazyImageSrc()` that, for each `img`, prefers `data-echo`/`data-src`/`data-original`/`data-lazy-src`, resolves it to an absolute http(s) URL, and overwrites `src` before Defuddle runs. Now clipped `news/a/` articles emit the REAL `dingyue.ws.126.net/...` URL, not `empty.png`. Covered by `extractor/test/fixtures/netease-lazy-image.html` + unit test (23/23 pass).
- **Operational consequence**: After this fix, a netease `news/a/` clip that previously showed `empty.png` will now show a REAL remote image URL. That real URL is still a remote link unless `--save-images yes` localizes it.
- **When `empty.png` STILL appears**: only if the source page genuinely has no lazy-load real URL (rare). It remains a 1×1 pixel, NOT a real figure — multiple copies per article.
- **Localizing decision**: stores N identical transparent pixels under `Inbox/images/` and rewrites links. No informational value. When the user asks to "save images locally" but ALL detected remote images are `empty.png`, confirm before localizing; recommend keep-remote / abandon. If real `dingyue.ws.126.net` URLs are present, localizing them is meaningful.

## Note on tracking params
- `canonicalUrl` strips tracking params; `webclip_id` keys off it → no duplicate-note risk from `?spss=...`.
