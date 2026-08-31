import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import { extractHtml, extractUrl, meetsQualityGate } from '../src/extractor.mjs';

const fixtureUrl = new URL('./fixtures/article.html', import.meta.url);
const fixtureHtml = await readFile(fixtureUrl, 'utf8');

test('extractHtml uses Defuddle to produce article metadata and Markdown', async () => {
  const result = await extractHtml(fixtureHtml, 'https://Example.com/articles/secure-clipping?utm_source=test#intro');

  assert.equal(result.title, 'Secure Web Clipping Without Surprises');
  assert.equal(result.author, 'Ada Example');
  assert.equal(result.published, '2026-07-20');
  assert.equal(result.description, 'A practical guide to clipping articles without trusting the network.');
  assert.equal(result.site, 'Example Security Journal');
  assert.equal(result.canonicalUrl, 'https://example.com/articles/secure-clipping');
  assert.deepEqual(result.keywords, ['security', 'clipping', 'obsidian']);
  assert.match(result.markdown, /Secure defaults reduce the attack surface/);
  assert.match(result.markdown, /\[network policy\]\(https:\/\/example\.com\/guides\/network-policy\)/);
  assert.ok(result.markdown.length >= 200);
  assert.ok(result.wordCount >= 40);
});

test('quality gate requires a meaningful title and at least 200 Markdown characters', () => {
  assert.equal(meetsQualityGate({ title: 'A useful article', markdown: 'x'.repeat(200) }), true);
  assert.equal(meetsQualityGate({ title: 'Untitled', markdown: 'x'.repeat(300) }), false);
  assert.equal(meetsQualityGate({ title: 'Useful', markdown: 'x'.repeat(199) }), false);
  assert.equal(meetsQualityGate({ title: '  ', markdown: 'x'.repeat(300) }), false);
});

test('extractUrl returns static method when the static extraction passes quality', async () => {
  const result = await extractUrl('https://example.com/article', {
    allowBrowser: false,
    fetchHtml: async () => ({ html: fixtureHtml, finalUrl: 'https://example.com/articles/secure-clipping' }),
  });

  assert.equal(result.method, 'static');
  assert.equal(result.title, 'Secure Web Clipping Without Surprises');
});

test('extractUrl fails closed when static quality is insufficient and browser use is disabled', async () => {
  await assert.rejects(
    extractUrl('https://example.com/short', {
      allowBrowser: false,
      fetchHtml: async () => ({
        html: '<html><head><title>Short</title></head><body><main><p>Too short.</p></main></body></html>',
        finalUrl: 'https://example.com/short',
      }),
    }),
    error => error.code === 'QUALITY_GATE',
  );
});

const neteaseFixtureUrl = new URL('./fixtures/netease-article.html', import.meta.url);
const neteaseFixtureHtml = await readFile(neteaseFixtureUrl, 'utf8');

test('extractHtml falls back to embedded __INITIAL_STATE__ author on Netease pages', async () => {
  const result = await extractHtml(neteaseFixtureHtml, 'https://c.m.163.com/news/a/L5BC6R9V0553K5ZV.html');

  assert.equal(result.title, '40万亿美债要是守不住，最后的杀手锏就是出动军队化解美债？');
  assert.equal(result.author, '流苏晚晴');
  assert.equal(result.published, '2026-08-27 12:32:05');
  assert.equal(result.site, 'c.m.163.com');
});

test('extractHtml keeps standard author metadata over embedded Netease state', async () => {
  const html = `<!doctype html>
<html>
<head><title>测试标题</title><meta name="author" content="标准作者"></head>
<body>
  <article>${'测试正文'.repeat(120)}</article>
  <script>window.__INITIAL_STATE__={"main":{"source":"内嵌作者","sourceinfo":{"tname":"内嵌作者"}}}</script>
</body>
</html>`;

  const result = await extractHtml(html, 'https://c.m.163.com/news/a/TESTID.html');
  assert.equal(result.author, '标准作者');
});

test('extractHtml tolerates broken __INITIAL_STATE__ without throwing', async () => {
  const html = `<!doctype html>
<html>
<head><title>测试标题</title></head>
<body>
  <article>${'测试正文'.repeat(120)}</article>
  <script>window.__INITIAL_STATE__={"main":{"source":"测试作者",}</script>
</body>
</html>`;

  const result = await extractHtml(html, 'https://c.m.163.com/news/a/TESTID.html');
  assert.equal(typeof result.author, 'string');
});

test('extractHtml only applies embedded-state fallback on 163.com hostnames', async () => {
  const html = `<!doctype html>
<html>
<head><title>测试标题</title></head>
<body>
  <article>${'测试正文'.repeat(120)}</article>
  <script>window.__INITIAL_STATE__={"main":{"source":"内部来源"}}</script>
</body>
</html>`;

  const result = await extractHtml(html, 'https://example.com/news/a/TESTID.html');
  assert.equal(result.author, '');
});
