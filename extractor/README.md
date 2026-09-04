# web-to-obsidian-extractor

The Node.js content-extraction engine for the web-to-obsidian Hermes plugin. It
fetches a public web page over a hardened network policy and returns normalized
article metadata plus Markdown.

See `../CHANGELOG.md` for version history and `../plugin/README.md` for how the
plugin drives this extractor.

## Requirements

- Node.js 18+ (package `engines.node: >=18`)
- Locked dependencies from `package-lock.json` (`npm ci --ignore-scripts`)

## Dependencies

| Package    | Purpose                                  |
|------------|------------------------------------------|
| `defuddle` | Primary static article extraction        |
| `linkedom` | Lightweight DOM parsing (no browser)     |
| `playwright` | Isolated Chromium fallback for weak pages |

## CLI

```bash
node src/cli.mjs <url> [--no-browser]
```

- `<url>` — exactly one `http://` or `https://` URL.
- `--no-browser` — skip the Chromium fallback; fail with `QUALITY_GATE` if the
  static pass yields no substantial article.

Output is a single JSON line on stdout. On success:

```json
{ "ok": true, "title": "...", "author": "...", "published": "...",
  "description": "...", "site": "...", "canonicalUrl": "...",
  "keywords": [...], "markdown": "...", "wordCount": 123, "method": "static" }
```

`method` is `static` (Defuddle over the pinned fetch) or `playwright`
(Chromium fallback). On failure, exit code is `1` and stdout carries:

```json
{ "ok": false, "error": "<user-facing message>", "code": "<CODE>" }
```

### Error codes

| Code                       | Meaning                                          |
|----------------------------|--------------------------------------------------|
| `INVALID_URL`              | The URL is malformed.                            |
| `UNSUPPORTED_SCHEME`       | Only HTTP and HTTPS are allowed.                 |
| `URL_CREDENTIALS`          | URLs containing credentials are rejected.        |
| `NON_DEFAULT_PORT`         | Non-default ports are rejected.                  |
| `DNS_FAILED`               | The hostname could not be resolved.              |
| `BLOCKED_ADDRESS`          | Destination blocked by network policy.           |
| `TOO_MANY_REDIRECTS`       | The page redirected too many times.              |
| `INVALID_REDIRECT`         | The page returned an invalid redirect.           |
| `TIMEOUT`                  | Extraction timed out.                            |
| `NETWORK_ERROR`            | The page request failed.                         |
| `HTTP_STATUS`              | The server returned an unsuccessful status.      |
| `UNSUPPORTED_CONTENT_TYPE` | The response is not HTML.                        |
| `UNSUPPORTED_ENCODING`     | The response uses an unsupported encoding.       |
| `BODY_TOO_LARGE`           | The response body is too large.                  |
| `INVALID_RESPONSE`         | The server returned an invalid response.         |
| `EXTRACTION_FAILED`        | Article extraction failed.                       |
| `QUALITY_GATE`             | The page did not contain a substantial article.  |
| `BROWSER_FAILED`           | Browser extraction failed.                       |
| `USAGE`                    | Bad command-line invocation.                     |

## Extraction pipeline

1. **Static pass** — `extractHtml()` normalizes links (`absolutizeLinks`),
   canonicalizes the URL, then runs Defuddle to produce Markdown. No page
   scripts execute and Defuddle network fallbacks are disabled.
2. **Quality gate** — a result passes only if it has a meaningful non-generic
   title and ≥ 200 Markdown characters (`MIN_MARKDOWN_CHARS`).
3. **Playwright fallback** (unless `--no-browser`) — if the static pass fails
   the quality gate, an isolated headless Chromium re-renders the page. All
   browser HTTP(S) is deferred to the pinned Node fetch layer via
   `buildSecureRouteHandler()` (see [Network safety](#network-safety)).

### Site-specific handling

- **Netease (`c.m.163.com`)** — author and published date live only in the
  embedded `window.__INITIAL_STATE__` JSON, not in standard meta tags.
  `extractNeteaseMeta()` reads the balanced `main` object (source /
  `sourceinfo.tname` / `ptime`), scoped to `163.com` hostnames.
- **Lazy-loaded images** — Netease and similar sites render images with a
  placeholder `src` (`empty.png`) and the real URL in `data-echo` /
  `data-src` / `data-original`. `extractLazyImageSrc()` prefers those
  attributes (in that order) and rewrites `src` to the real, same-page
  absolute URL before Defuddle.

## Network safety

- Every request is validated and pinned: non-default ports rejected, redirects
  revalidated, DNS answers must all be public, and each request is bound to an
  approved address while preserving TLS SNI/hostname checks.
- Static responses require an HTML media type and a bounded body.
- In the browser, **Chromium native DNS and HTTP(S) are disabled**
  (`--host-resolver-rules=MAP * ~NOTFOUND`). Every HTTP(S) page resource is
  fetched by the pinned Node layer and injected via `route.fulfill`;
  WebSockets, service workers, and downloads are blocked. Request count,
  per-resource bytes, total bytes, and wall time are bounded.
- The naked `node src/cli.mjs` process may print page-controlled diagnostics
  only internally; the CLI protocol and consuming logs stay free of untrusted
  stack traces.

See `src/network-policy.mjs` for the full policy implementation.

## Tests

```bash
npm test        # node --test (requires locked deps installed)
npm run check   # node --check on each src module
```

Test files live in `test/` and cover the CLI protocol, extraction, network
policy, and security regressions.