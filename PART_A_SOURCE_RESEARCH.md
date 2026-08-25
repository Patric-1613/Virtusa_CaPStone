# Part A — Feasible Sources for the AI Daily Digest

Validated/researched: 23 August 2026

## Recommendation in one sentence

Build the first version from official RSS feeds and official release-note pages, add GitHub/PyPI release data for open-source tools, and use small page-specific scrapers only for companies such as Anthropic that do not publish a usable feed.

Do not buy a general news API or use an LLM-powered search API for the MVP. Those can help discover stories later, but they add cost and make attribution, completeness, and duplicate handling harder.

## The important distinction

There are two different kinds of update:

1. **Company announcements** — model launches, research posts, product announcements, policy, partnerships.
2. **Technical changes** — API features, model IDs, deprecations, SDK/package versions, breaking changes.

A company blog alone does not reliably cover technical changes. For a useful “what changed?” product, collect at least one announcement source and one technical source for each major vendor.

## Recommended MVP source registry

| Priority | Company/topic | Source | Access | Auth | What it contributes | Notes |
|---|---|---|---|---|---|---|
| P0 | OpenAI | `https://openai.com/news/rss.xml` | RSS | None | Official announcements and research | Best first collector; filter non-technical company posts after ingestion. |
| P0 | OpenAI | `https://openai.com/products/release-notes/` | Release-notes page/RSS link exposed by page | None | API, Codex and ChatGPT changes | Prefer the page's published RSS link when discovered; otherwise parse the dated list. |
| P0 | Anthropic | `https://www.anthropic.com/news` | HTML/Next.js page | None | Official Claude/product/research announcements | No dependable native RSS was found; this is one justified scraper. |
| P0 | Anthropic | `https://docs.claude.com/en/release-notes/overview` | Docs page | None | Claude Developer Platform and app changes | A better source for API changes than the newsroom. |
| P0 | Google Gemini | `https://blog.google/technology/ai/rss/` | RSS | None | Google AI product announcements | Official Google topic feed. |
| P0 | Google DeepMind | `https://deepmind.google/blog/rss.xml` | RSS | None | Model and research announcements | Includes Gemini, Gemma and research posts. |
| P0 | Gemini API | `https://ai.google.dev/gemini-api/docs/changelog` | Changelog page | None | Model launches, deprecations and API changes | Dated entries make change extraction straightforward. |
| P0 | xAI/Grok | `https://docs.x.ai/developers/release-notes` | Changelog page | None | Grok model/API releases and deprecations | Prefer this for technical facts. |
| P1 | xAI/Grok | `https://x.ai/news` | HTML page | None | Product and company announcements | Use a small adapter only if release notes are not enough. |
| P0 | LangChain ecosystem | `https://docs.langchain.com/oss/python/releases/changelog` | Changelog + RSS offered by page | None | LangChain, LangGraph and Deep Agents changes | Official changelog explicitly advertises an RSS feed. |
| P0 | LangChain packages | `https://pypi.org/rss/project/langchain/releases.xml` | RSS | None | Exact package releases | Repeat for `langgraph` and `deepagents`; useful for machine-verifiable version changes. |
| P1 | LangChain GitHub | `https://api.github.com/repos/langchain-ai/langchain/releases` | REST API | Optional token | Structured release name, tag, body, date and URL | Public requests work without auth; a token gives more capacity. |
| P1 | Meta Llama | `https://ai.meta.com/blog/` | HTML page | None | Official Llama announcements and research | Use after the reliable P0 collectors work. |
| P1 | Meta Llama | Official Meta Llama GitHub repositories via the GitHub Releases API | REST API | Optional token | Versioned code/model-tool releases | Store the exact repository as source metadata; do not mix community forks with official repos. |
| P1 | DeepLearning.AI | `https://www.deeplearning.ai/the-batch` | HTML page or dedicated newsletter mailbox | None/mail credentials | Editorial context and weekly industry coverage | Treat as secondary reporting/opinion, not proof of a vendor claim. |
| P1 | Hugging Face | `https://huggingface.co/blog/feed.xml` | RSS | None | Open-source model/tool ecosystem | High volume; rank/filter community posts. |
| P2 | arXiv | `https://rss.arxiv.org/rss/cs.CL`, `cs.AI`, `cs.LG` | RSS | None | New research papers | Optional discovery source; too noisy for the two-week MVP. |

## Best five collectors to implement first

These give good coverage while demonstrating three ingestion methods:

1. OpenAI News RSS.
2. Google DeepMind RSS.
3. Anthropic News page adapter.
4. Gemini API changelog adapter.
5. GitHub Releases API for one LangChain repository.

Then add the LangChain/PyPI RSS feeds because they are very cheap to support once an RSS collector exists.

## Is web scraping useful?

Yes, but as a fallback rather than the foundation.

Use this order:

1. Official RSS/Atom feed.
2. Official JSON/REST API.
3. Official changelog or a page containing machine-readable JSON-LD/Next.js data.
4. Targeted HTML scraping.
5. Newsletter mailbox ingestion.
6. Third-party news/search API for discovery only.

Scraping is reasonable when:

- the source is public and the site permits automated access;
- no feed/API exists;
- requests are infrequent (for example once daily);
- the collector identifies itself with a sensible user agent;
- it obeys `robots.txt`, terms, rate limits, and copyright constraints;
- failures are isolated so one layout change does not stop the whole run;
- you keep only what the project needs and link back to the original.

Do not scrape Google search result pages, X timelines, or arbitrary news sites for the MVP. They are unstable, often restricted, noisy, and much harder to defend academically.

## API-key decision

You do **not** need OpenAI, Anthropic, Gemini, or xAI model API keys to collect their public announcements. Their inference APIs generate model outputs; they are not news databases.

Useful keys later:

- **GitHub token:** optional but useful for higher API limits and reliable polling.
- **Email provider credentials:** only if ingesting newsletters or sending the digest.
- **LLM provider key:** Person B needs this for extraction/summarisation, not for basic collection.
- **News/search API key:** optional discovery layer after the official-source MVP works.

All secrets must live in environment variables or a local `.env` excluded from Git—not in `sources.yaml` or committed code.

## Standard record Person A should output

This research note is background guidance. `docs/API_CONTRACT.md` is the normative shared
contract. Person A emits a stable source item and an immutable snapshot as separate records; a URL
hash is a deduplication key, not the resource ID.

Source item:

```json
{
  "id": "7c443d0d-8c7e-4cb0-b55d-16df91a40da1",
  "dedupe_key": "sha256:canonical-url-hash",
  "source_id": "openai_news",
  "source_type": "rss",
  "publisher": "OpenAI",
  "title": "Example title",
  "canonical_url": "https://...",
  "published_at": "2026-08-23T09:00:00Z",
  "updated_at": null,
  "first_fetched_at": "2026-08-23T10:00:00Z",
  "summary": "Source-provided description, if present",
  "latest_snapshot_id": "96377473-b3ac-4133-9f7d-63f28edbdc39",
  "event_id": null,
  "language": "en",
  "tags": ["model-release"]
}
```

Document snapshot:

```json
{
  "id": "96377473-b3ac-4133-9f7d-63f28edbdc39",
  "source_item_id": "7c443d0d-8c7e-4cb0-b55d-16df91a40da1",
  "fetched_at": "2026-08-23T10:00:00Z",
  "content_text": "Clean article or release-note text",
  "content_hash": "sha256:clean-content-hash",
  "raw_location": "raw/openai_news/2026-08-23/...json",
  "etag": null,
  "last_modified": null,
  "collector_version": "1.0.0"
}
```

Fields required for change tracking are `canonical_url`, `published_at`, `fetched_at`, and `content_hash`. If a page changes at the same URL, keep the old snapshot; never overwrite it.

## Storage rule

A vector database should be a **search index**, not the only database.

Keep:

- immutable raw responses/snapshots for audit;
- normalized article metadata in a relational/document store;
- embeddings plus record IDs in the vector store.

This lets the team prove exactly what a source said at a particular time. Embedding similarity by itself cannot reliably reconstruct field-level changes such as price, context window, model availability, or a deprecation date.

## Deduplication strategy

Apply in this order:

1. Normalize the URL: remove tracking parameters and fragments; resolve redirects.
2. Exact match on canonical URL.
3. Exact match on normalized content hash.
4. Near-duplicate candidate if title similarity is high and publication times are close.
5. Keep separate records for different publishers covering the same event, but assign a shared `event_id` later.

Do not delete secondary coverage merely because it describes the same announcement. A duplicate article and two independent sources about one event are different things.

## Reliability requirements

Each collector should record:

- HTTP status, latency and fetch time;
- returned item count;
- last successful run;
- parser version;
- ETag and Last-Modified, when provided;
- parse failures and reason;
- zero-item anomaly (a successful request returning no entries).

Use conditional requests (`If-None-Match`/`If-Modified-Since`) and per-source retries with exponential backoff. A source failure should create an error record and allow the remaining collectors to finish.

## Acceptance tests for Part A

Part A is complete when:

- five real sources run from one command;
- at least two are RSS, one is an API, and one is a page/changelog adapter;
- every output record validates against the agreed schema;
- re-running the collectors creates no duplicate records;
- a changed page creates a new snapshot rather than overwriting history;
- one intentionally broken source does not stop the other collectors;
- every stored item has a working provenance URL and fetch timestamp;
- the run produces a short machine-readable success/failure report.

## Two-week scope warning

Do not attempt every company immediately. Five dependable sources with good traceability are a stronger submission than twenty fragile scrapers. Once the collector interface is stable, additional sources are small configuration/adaptor tasks.

## Evidence links

- OpenAI official news and communications: https://openai.com/news and https://help.openai.com/en/articles/11725090
- OpenAI release notes: https://openai.com/products/release-notes/
- Anthropic newsroom: https://www.anthropic.com/news
- Claude documentation/release notes: https://docs.claude.com/en/home
- Gemini API changelog: https://ai.google.dev/gemini-api/docs/changelog
- Google-wide product RSS: https://blog.google/feed/
- Google DeepMind news: https://deepmind.google/blog/
- xAI developer release notes: https://docs.x.ai/developers/release-notes
- LangChain changelog: https://docs.langchain.com/oss/python/releases/changelog
- GitHub Releases API: https://docs.github.com/en/rest/releases/releases
- GitHub conditional request guidance: https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api
- PyPI RSS feed documentation: https://docs.pypi.org/api/feeds/
- DeepLearning.AI The Batch: https://www.deeplearning.ai/the-batch
