# assets/vendor - bundled third-party front-end libraries (offline use)

index.html used to load these from cdn.jsdelivr.net. They are vendored here byte-for-byte
(downloaded 2026-11-09 with `curl -L`, version taken from jsDelivr's `x-jsd-version` header) so the
app works offline and makes no external network requests.

| File | Library / version | Source URL | License |
|---|---|---|---|
| marked.min.js | marked 15.0.12 | https://cdn.jsdelivr.net/npm/marked/marked.min.js (unpinned URL; resolved to 15.0.12 on the download date) | MIT |
| highlight.min.js | highlight.js 11.9.0 | https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/highlight.min.js | BSD-3-Clause |
| github-dark.min.css | highlight.js 11.9.0 theme | https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/styles/github-dark.min.css | BSD-3-Clause |
| mermaid.min.js | mermaid 10.9.8 | https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js (resolved to 10.9.8 on the download date) | MIT |
| purify.min.js | DOMPurify 3.4.17 (published 2026-10-12) | https://cdn.jsdelivr.net/npm/dompurify@3.4.17/dist/purify.min.js | Apache-2.0 / MPL-2.0 |

## SHA256
```
3e7e7d7feb3e5d58cb6c804f68ab5c24cc7e5eb6270fd6e5cbb9124739217d0c *marked.min.js
837a6fa5b0c736b52bbde2b2b6190f305da3fc9ed41681db5321507057b5c846 *highlight.min.js
9f208d022102b1d0c7aebfecd8e42ca7997d5de636649d2b31ea63093d809019 *github-dark.min.css
8d607d7ef1d077a8aa202e18e62212bfa992c68bfeabc5cf45d51a128fe6675d *mermaid.min.js
cd37898f9782adf54820c413191b5691803f0f137c799d6edcfd38523b4c2c31 *purify.min.js
```
