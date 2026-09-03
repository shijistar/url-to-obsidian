# Zhihu cookie completeness vs. successful fetch

Use this note when a Zhihu article clip/retry task involves user-supplied cookies or an auxiliary scraper such as `zhihu-scraper`.

## Durable lessons

1. Judge cookie completeness from the **actual cookie text received in chat**, not from assumptions about the user's browser state.
2. `z_c0` missing from the received text is a valid reason for a first failure report, but that conclusion must be **retracted/updated immediately** if the user later supplies `z_c0`.
3. `zhihu-scraper check` reporting:
   - `Cookie 字段 z_c0、d_c0 均已配置`
   - `知乎登录状态有效`
   does **not** prove article fetch will succeed.
4. Treat these as separate checkpoints:
   - cookie completeness
   - login/session validity
   - protocol fetch success
   - browser fallback success
5. On Zhihu, protocol fetch may still return `HTTP 403` even after `z_c0 + d_c0 (+ q_c1)` are present and login check passes.
6. Browser fallback may fail differently from protocol mode, e.g. page initial state missing the expected `articles:<id>` payload.
7. Therefore, when reporting status to the user, explicitly distinguish:
   - “登录态有效”
   - “正文抓取成功 / 失败”

## Recommended verification sequence

1. Parse the user-provided cookie text and enumerate cookie names.
2. If using `zhihu-scraper`, run `zhihu check` first.
3. Run protocol mode fetch (`--browser never`).
4. If needed, run browser fallback (`--browser always`).
5. Only claim success if one of the fetch paths returns article content.
6. If all live fetches fail but cached HTML/Markdown exists, use the cache as fallback and clearly label it as a fallback.

## Session-derived example outcomes

- Missing `z_c0` in the received text -> login invalid / fetch blocked.
- Later supplying `z_c0` -> login valid.
- Despite valid login:
  - direct HTTP request still `403`
  - `zhihu-scraper --browser never` still `403`
  - `zhihu-scraper --browser always` may still fail to locate `articles:<id>` in page state.

## Reporting rule

Never collapse these into one statement like “cookies are fine so the scraper should work.”
Instead say exactly which layer passed and which layer failed.