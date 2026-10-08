# Demand Radar

> An open-source, self-hosted research tool that studies how people describe
> problems in their own words across public online communities.

**Status:** Phase 1 (Research MVP) is implemented and tested. See [ROADMAP.md](ROADMAP.md) for later phases.

Demand Radar is a **personal, non-commercial, open-source project**. It is a
local command-line tool: it runs on your own machine, stores results in a local
SQLite file, and is **strictly read-only** against every source it reads — it
never posts, comments, votes, or sends messages anywhere. See
[Responsible use](#responsible-use).

---

## Why this project exists

Keyword and brand monitoring answers "who mentioned us." This project is built
around a different question:

> **How do people describe a problem when they do not yet know the vocabulary
> for it?**

People rarely describe a difficulty in the terms an outsider would search for.
Someone who says *"I freeze when the buyer pushes back"* is describing a problem
that no keyword list for it would catch. The goal is to read public conversation
closely enough to recognise the underlying need, preserve the original wording,
and tell an honest story about how certain each conclusion is — so that research
rests on what people actually said rather than on assumptions.

---

## Quick start

Demand Radar runs with **no credentials**: Hacker News needs no key, and the first-stage relevance filter is local.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Collect, screen, and report in one pass
demand-radar run examples/non-native-negotiation.yaml --period 6m
```

To enable structured classification, copy `.env.example` to `.env` and set `GEMINI_API_KEY`.

### Commands

The CLI maps onto the two planes rather than hiding them:

```bash
demand-radar sources                        # list collectors and their config needs
demand-radar collect hypothesis.yaml        # Data Plane only — fetch and store
demand-radar screen  hypothesis.yaml        # Intelligence Plane only — no network
demand-radar run     hypothesis.yaml        # both, then report
demand-radar report  hypothesis.yaml        # read stored results only
demand-radar signals                        # what has been collected, any hypothesis
```

Useful options: `--period` (`today`, `7d`, `6m`, `1y`), `--format` (`table`, `json`, `csv`, `signals-json`), `--out`, `--db`, `--reclassify`.

---

## Architecture

The roadmap's central requirement is that **collection is separated from intelligence**. That boundary is enforced by package structure, not convention:

```text
src/demand_radar/
├── domain/              # shared vocabulary. No I/O, no HTTP, no SQL, no model calls.
│   ├── signal.py        #   Signal — one public utterance, nothing inferred
│   ├── hypothesis.py    #   Hypothesis + TimeWindow — the research question
│   └── classification.py#   Classification — derived dimensions, per hypothesis
│
├── data_plane/          # sources → collectors → normalise → dedupe → store
│   ├── collectors/      #   one module per source, no demand logic allowed
│   ├── storage/         #   SQLite: signals / classifications / collection_state
│   └── collection.py    #   the collection run: paging, retries, isolation
│
├── intelligence/        # hypotheses → relevance → classification → reporting
│   ├── relevance.py     #   stage 1: cheap, local, deterministic
│   ├── jev_schema.py    #   stage 2: our dimensions as typed Jev questions
│   ├── jev.py           #   stage 2: the TypeSafe Jev decider
│   ├── screening_schema.py #  stage 3: free-text schema + strict parsing
│   ├── providers.py     #   stage 3: Gemini transport (+ a null default)
│   ├── screening.py     #   orchestrates the three stages
│   └── reporting.py     #   pain × role × language × source × community
│
├── cli/                 # terminal interface and rendering only
└── config.py            # environment configuration
```

### What the separation buys

**One dataset, many hypotheses.** A signal is collected once and stored with its raw payload. Each hypothesis classifies it independently, keyed by `(signal_id, hypothesis)`. Screening a second hypothesis costs model calls — never refetching:

```bash
demand-radar run    first-hypothesis.yaml    # fetches
demand-radar screen second-hypothesis.yaml   # no network at all
```

**Raw data is never overwritten by conclusions.** `signals` and `classifications` are separate tables. Deleting every verdict for a hypothesis (`--reclassify`) loses no collected data.

### Design decisions worth knowing

**Deduplication is by content identity.** `Signal.id` hashes the source's own id, else the URL, else the content — deliberately *not* the query, so the same post found by two expanded queries converges on one row.

**Staged by cost — three stages, each paying only for what the last one kept.**

| Stage | What it is | Speed | Decides |
|---|---|---|---|
| 1 | local filter | instant, free | drops empty, short, promotional |
| 2 | **TypeSafe Jev** | 250–450 ms | relevance + every enumerable dimension |
| 3 | Gemini | seconds | open-ended text and verbatim quotes |

Jev carries the bulk of the work. It is a System One model — it *chooses* from
defined options rather than generating text, and every question in a request is
evaluated in parallel, so one call settles relevance, pain type, intent,
urgency, and four boolean flags at once. The generative model is then asked only
about signals Jev already judged relevant, and only for what Jev structurally
cannot produce: roles, industries, language codes, quotes.

On live data that means a run where Jev rejects everything costs **zero**
generative calls. Jev's typed answers win on every dimension it can decide;
the generative model never overrides them.

Every stage is optional. With no keys the cheap verdict is recorded as-is; with
Jev alone the classification is complete except its text fields. `Relevance.stage`
records which stage decided (`cheap`, `jev`, `jev+generative`), so a dataset is
always honest about how it was judged.

**Confidence is distribution shape, not likelihood.** Jev returns `1.0` when all
probability sits on one outcome and `0.0` when it is spread evenly. Below `0.5`
the answer is the model saying it does not know, so the dimension is recorded as
`None` rather than its most-likely guess — the roadmap's "preserve uncertainty"
rule on a measured footing rather than the model's discretion.

**The cheap filter is generous on purpose.** Latent demand is usually phrased without the vocabulary we expect ("I freeze when the buyer pushes back"), so a precise first stage would discard exactly what the project exists to find. Precision is stage 2's job — and when no model is configured, unmarked prose is *rejected* rather than passed, because "relevant" must never rest on text length alone.

**Uncertainty is preserved, never guessed.** Every inferred dimension is nullable end to end — schema, parser, SQL, and export. A NULL boolean reads back as `None`, not `False`, and `unknown` appears in breakdowns because missing coverage is itself a finding.

**Quotes are verified verbatim.** A model-returned quote is kept only if it genuinely appears in the signal, so a paraphrase cannot contaminate the Voice of Customer library.

**Historical correctness.** A window resolves to absolute bounds once, up front. Signals are filtered against it locally as well as in the source query, so a source that ignores date bounds cannot leak later data into an earlier period's analysis.

**Failures are isolated and visible.** One source failing (rate limit, missing credential, network) never sinks a run; the error is recorded against that source. A model failure degrades to the cheap verdict rather than discarding the signal, and the error is surfaced.

---

## Writing a hypothesis

```yaml
name: non-native-negotiation
statement: >
  B2B professionals who struggle to negotiate in a language that is not
  their native language.

queries:                  # expanded, multilingual; language is first-class
  - non-native speaker
  - second language
  - verhandlung
sources: [hackernews]
relevance_criteria: >     # natural-language test, applied at screening
  The author describes their own difficulty in a professional conversation
  conducted in a language they are not fluent in.
languages: []             # empty means any
```

### Targeting communities

Which communities to search is part of the research question, so it lives in the
hypothesis rather than the environment — credentials stay in `.env`:

```yaml
sources: [reddit]
source_options:
  reddit:
    subreddits: [r/sales, r/consulting, r/msp]
    include_comments: true     # comments are where people describe their own difficulty
```

Each subreddit is searched separately with `restrict_sr`, so Reddit cannot widen
the query back out to unrelated communities. Names are accepted as `r/sales`,
`/r/sales`, or `sales`. The per-page budget is split across subreddits so one
busy community cannot starve the others, and an exhausted subreddit drops out of
later pages instead of restarting.

See `examples/sales-pushback-reddit.yaml` for a complete hypothesis.

Keep query terms **short and distinctive**. Sources rank loosely rather than matching phrases, so a collector requires every term in a query to appear — a long sentence will quietly match nothing. A zero-result run says so explicitly.

---

## Sources

| Source | Config | Notes |
|---|---|---|
| Hacker News | none | Public Algolia API. Works on a clean clone, no account needed. |
| Reddit | OAuth credentials | Read-only search, site-wide or inside named subreddits. See below. |
| RSS / Atom | feed URLs | Any feed; set `DEMAND_RADAR_RSS_FEEDS`. |

Every adapter is read-only: it issues search and fetch requests and nothing else.
Adding a source is one class plus a registry entry, and adapters must contain no
demand-classification logic.

### Reddit access

The Reddit adapter uses Reddit's official Data API over OAuth
(`grant_type=client_credentials`, app-only). It calls exactly two endpoints:

```
GET https://oauth.reddit.com/r/{subreddit}/search   (restrict_sr=true)
GET https://oauth.reddit.com/search
```

It contains no code that posts, comments, votes, messages, or modifies anything
on Reddit.

Access to Reddit's Data API requires approval under Reddit's
[Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy);
set `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` in `.env` once you have it.
Without Reddit credentials the rest of the tool still works — Hacker News needs
no key at all, and a failing or unconfigured source never sinks a run.

---

## Development

```bash
pytest          # 401 tests, fully offline (no API keys needed or used)
ruff check .
```

Tests never read ambient credentials: the screening fixtures inject an explicitly
disabled Jev decider, so a developer with real keys in `.env` cannot accidentally
make live calls from the suite.

### Watching a run

`--verbose` is the dev view — one line per decision showing which stage decided,
how certain it was, and how long it took:

```
screening start hypothesis=non-native-negotiation pending=14 jev=jev-latest generative=gemini-2.5-flash
d9a3d39efa07b223 relevant=0.01 → dropped  477ms
27c6f824e8b52b72 relevant=0.05 → dropped  281ms
a3f2b1c8d9e04f17 relevant=0.96 pain=expression(0.91) urgency=high(0.74) b2b=True  263ms
screening done examined=14 jev_calls=13 jev_rejected=12 generative_calls=1 relevant=1 cost_usd=0.000723
```

Tests mirror the source layout under `tests/domain`, `tests/data_plane`, `tests/intelligence`, and `tests/cli`.

---

## Responsible use

This project reads public conversations written by real people. That carries
obligations, and they are design constraints here rather than a policy page.

**Read-only, everywhere.** No adapter posts, comments, votes, follows, or sends
messages. There is no code path that writes to any source.

**Within each source's own rules.** Access goes through official APIs with
proper authentication — never scraping around them, and never circumventing rate
limits. Reddit access additionally requires approval under its
[Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy).

**Deleted means deleted.** If someone removes their post or comment at the
source, remove it locally too. Re-run collection periodically rather than
treating the local database as a permanent archive.

**Not for training models.** Collected content is for reading and analysis by a
person. Do not use it as training data for machine-learning or AI models — most
sources, Reddit included, prohibit this outright.

**No profiling of individuals.** The unit of analysis is the problem being
described, not the person describing it. Do not use this to infer sensitive
characteristics about anyone, to re-identify people, or to match them against
off-platform identifiers.

**Local-first.** Data stays in a local SQLite file on your own machine. Nothing
is uploaded, shared, or sold.

**Not an outreach tool.** This exists to help someone understand a problem
space, not to build contact lists or message anyone.

---

## Attribution

Architectural patterns for multi-source ingestion, source adapters, normalization, deduplication, local storage, and CLI structure are adapted from [Harken](https://github.com/VladUZH/harken) (MIT License).

## License

MIT — see [LICENSE](LICENSE).
