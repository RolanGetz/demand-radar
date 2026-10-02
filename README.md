# Demand Radar

> Open-source demand intelligence framework for discovering where real user needs, pain points, and emerging opportunities appear across public online communities.

## Why this project exists

Most distribution decisions are still made from intuition:

- Which communities should we enter?
- Which languages and markets deserve attention?
- What problems are people actively discussing?
- Which pain points are growing over time?
- Where is there strong demand but weak competition?
- Which channels should we invest in before building an audience there?

Traditional social listening tools are usually optimized for brand mentions, keywords, sentiment, or competitor monitoring.

Demand Radar is built around a different question:

> **Where are people expressing a real need, even when they do not know the product or category name yet?**

The goal is to turn public conversations into structured demand signals that can guide distribution, product positioning, market research, and channel development.

---

## Core idea

A user starts with a hypothesis, for example:

> B2B professionals who struggle to negotiate in a language that is not their native language.

Demand Radar then:

1. Expands the hypothesis into multilingual search queries and semantic patterns.
2. Collects public discussions from supported sources.
3. Normalizes and deduplicates the data.
4. Filters irrelevant results.
5. Classifies each signal by pain, role, language, intent, urgency, and other dimensions.
6. Groups similar signals into semantic clusters.
7. Tracks frequency and change over time.
8. Produces a research view of where demand exists and which channels may be worth testing.

The desired output is not just a list of posts.

It is a structured map such as:

```text
pain × role × language × source × market × intent × time
```

---

## What we want to learn

Demand Radar should help answer questions such as:

- What problems are people repeatedly describing?
- How do people describe those problems in their own words?
- Which problems are increasing in frequency?
- Which professional roles experience them most often?
- Which language combinations appear most frequently?
- Which communities contain the strongest concentration of relevant demand?
- Where are users actively looking for solutions?
- Where is competition already dense?
- Where do existing solutions disappoint users?
- Which channels should be tested for distribution?
- Which content themes are supported by real demand rather than assumptions?

---

## Goals

### 1. Detect latent demand

Go beyond exact keyword matching.

The system should identify discussions that express a relevant problem even when the author does not use the terminology we expect.

For example, these may describe the same underlying problem:

```text
"I freeze when the buyer pushes back in German."

"I understand the client, but I cannot answer quickly enough."

"I realized after the meeting that procurement changed what they promised."

"I keep losing context between enterprise calls."
```

The framework should recognize the underlying demand, not just the words.

### 2. Support multilingual research

Language should be a first-class dimension.

Demand Radar must not assume that English is the only target or second language.

A professional conversation can happen in any supported language, and the user's native language may also be any language.

Examples:

```text
Portuguese → English
Spanish → German
French → English
Polish → French
English → Japanese
```

The framework should be able to discover these patterns from data rather than hard-code them.

### 3. Separate collection from intelligence

The system should have two clear layers:

```text
Data Plane
Sources → Collectors → Normalization → Deduplication → Storage

Intelligence Plane
Hypotheses → Relevance → Classification → Clustering → Trends → Opportunities
```

This separation should make it possible to reuse collected data across many hypotheses without fetching the same content repeatedly.

### 4. Preserve original Voice of Customer

The original wording of relevant posts and comments is valuable.

Demand Radar should retain the source text and metadata so that later analysis can reveal:

- recurring vocabulary,
- user objections,
- unmet needs,
- positioning language,
- content ideas,
- landing-page copy,
- product terminology.

The system should not reduce everything to abstract scores.

### 5. Support historical and continuous research

Two modes are important.

#### Historical research

Analyze a defined period:

```text
today
7 days
30 days
6 months
12 months
custom range
```

#### Continuous radar

Continuously collect new signals and detect meaningful changes:

- a pain cluster is growing,
- a new problem appears,
- purchase intent increases,
- a competitor starts appearing more often,
- a new community becomes relevant,
- a language pair begins to emerge.

---

## Initial sources

The first versions should prioritize sources where useful public conversations are accessible and technically practical to collect.

Potential sources include:

- Reddit
- Hacker News
- YouTube
- RSS / Atom feeds
- Stack Overflow / Stack Exchange
- Mastodon
- Bluesky
- public forums
- additional sources where permitted by their APIs, terms, and access model

Source support should be modular.

A source adapter should not contain business logic about demand classification.

---

## Analysis pipeline

The target pipeline is:

```text
Hypothesis
    ↓
Query / Semantic Expansion
    ↓
Source Retrieval
    ↓
Normalization
    ↓
Deduplication
    ↓
Cheap Relevance Filter
    ↓
Structured Semantic Screening
    ↓
Classification
    ↓
Embedding / Clustering
    ↓
Trend Detection
    ↓
Demand / Opportunity Analysis
    ↓
Research Workspace
```

A likely implementation strategy is to use inexpensive deterministic or embedding-based filtering first, then apply more expensive models only to the reduced set of candidates.

Structured decision models such as TypeSafe Jev can be useful for repeated high-volume classification tasks.

Possible dimensions include:

```text
relevant
pain_type
role
industry
b2b_context
native_language
conversation_language
commercial_intent
urgency
solution_seeking
competitor_mentioned
existing_solution_dissatisfaction
distribution_opportunity
```

Not every field must be known for every signal.

Uncertainty should be preserved rather than replaced with guesses.

---

## UX direction

Demand Radar should support both CLI and web workflows.

### CLI

The CLI is useful for:

- development,
- scheduled jobs,
- automation,
- backfills,
- CI,
- experimentation,
- power users.

Possible interface:

```bash
demand-radar run hypothesis.yaml
demand-radar backfill --since 6m
demand-radar watch
demand-radar serve
```

### Web research workspace

The web interface should be the primary product experience.

It should focus on research, not crawler configuration.

Example workflow:

```text
New Research

Hypothesis:
"Professionals struggling with negotiations in a non-native language"

Period:
6 months

Sources:
Reddit, Hacker News, YouTube, RSS

Languages:
Any
```

Possible output:

```text
1,842 relevant signals
11 pain clusters
7 language patterns
23 communities
4 rising trends
```

Users should be able to drill down from aggregate insights to the original source material.

---

## Architecture principles

### Source adapters are replaceable

Collectors should be modular and independently maintainable.

### Hypotheses are reusable

One collected dataset should support many research hypotheses.

### Raw data is preserved

Derived classifications should not replace original content.

### AI is used selectively

Do not send every collected item directly to an expensive LLM.

Use staged filtering and structured models where possible.

### Historical correctness matters

When researching a past period, the system should avoid leaking future information into that analysis.

### Results should be explainable

Whenever possible, the user should be able to trace a conclusion back to the underlying signals.

### Local-first where practical

Research data should be easy to run and store locally.

Hosted deployments may be added later, but self-hosting should remain a first-class option.

---

## What this project is not

Demand Radar is not intended to be:

- a generic brand mention tracker,
- a simple keyword alert system,
- an automated spam or outreach bot,
- a tool for mass-posting promotional messages,
- a generic sentiment dashboard,
- a replacement for qualitative customer research.

Its purpose is to identify and structure demand signals so that humans can make better product and distribution decisions.

---

## Initial development strategy

This project is being built as a transformation of an existing internal codebase rather than as a direct fork of another project.

We intend to reuse ideas and, where appropriate, compatible open-source components from existing projects.

Projects currently used as references include:

### Harken

Useful reference areas:

- multi-source ingestion,
- source adapters,
- normalization,
- deduplication,
- local storage,
- CLI and dashboard patterns.

Harken is licensed under the MIT License.

### TradingAgents

Useful reference areas:

- provider / vendor abstraction,
- historical analysis windows,
- TypeSafe Jev screening,
- typed analytical stages,
- CLI and batch execution patterns.

TradingAgents is licensed under Apache License 2.0.

### OpenMagpie

Useful reference areas:

- feed / watch separation,
- reusable ingestion,
- natural-language relevance criteria,
- semantic filtering model.

OpenMagpie's open-source core is licensed under Apache License 2.0, with separately licensed enterprise code where applicable.

Any reused code must retain the attribution and license notices required by its original license.

---

## Near-term roadmap

### Phase 1 — Research MVP

- [ ] Define the core data model
- [ ] Implement `Hypothesis`
- [ ] Add Reddit collector
- [ ] Add Hacker News collector
- [ ] Add RSS / Atom collector
- [ ] Normalize all source items into a shared schema
- [ ] Store raw and normalized data locally
- [ ] Add deduplication
- [ ] Add date-range backfills
- [ ] Add relevance screening
- [ ] Add structured classification
- [ ] Build a minimal CLI
- [ ] Produce CSV / JSON research output

### Phase 2 — Intelligence

- [ ] Semantic clustering
- [ ] Pain taxonomy
- [ ] Role detection
- [ ] Language-pair detection
- [ ] Purchase-intent detection
- [ ] Competitor and solution detection
- [ ] Existing-solution dissatisfaction
- [ ] Trend analysis
- [ ] Opportunity scoring
- [ ] Cross-source aggregation

### Phase 3 — Research workspace

- [ ] Web dashboard
- [ ] Research projects
- [ ] Saved hypotheses
- [ ] Historical comparison
- [ ] Trend visualization
- [ ] Source drill-down
- [ ] Filters by language, role, source, and pain
- [ ] Continuous monitoring
- [ ] Alerts for meaningful changes

### Phase 4 — Distribution intelligence

- [ ] Community discovery
- [ ] Channel scoring
- [ ] Content opportunity detection
- [ ] Voice-of-Customer library
- [ ] Market / language comparison
- [ ] Distribution experiment tracking

---

## Example use cases

### Product discovery

> Is there recurring demand for a tool that remembers changing stakeholder positions across long B2B deals?

### Distribution research

> Which communities contain the highest concentration of non-native professionals discussing difficult sales conversations?

### Market selection

> Is demand stronger among Portuguese-speaking, Spanish-speaking, or German-speaking professionals?

### Content strategy

> Which negotiation problems are being discussed repeatedly but are poorly covered by existing content?

### Competitive research

> What do users complain about when discussing existing meeting assistants, sales intelligence tools, and conversation products?

### Voice of Customer

> What exact language do users use when describing loss of context, pressure tactics, or difficulty responding in a second language?

---

## Project status

Early-stage research and development.

The architecture, naming, data model, and scoring system are expected to evolve significantly.

The immediate objective is not to build a large platform.

The immediate objective is to prove that a reusable pipeline can turn noisy public conversations into reliable, actionable demand intelligence.

---

## License

License for this project has not yet been finalized.

Before public release, all reused third-party code and dependencies must be reviewed for license compatibility and attribution requirements.

---

## Working principle

> **Do not decide where to distribute first. Measure where demand already exists.**
