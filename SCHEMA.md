# Demand Radar — Architecture Schema

Visual map of the architecture described in [README.md](README.md) and [ROADMAP.md](ROADMAP.md).

Legend: `[x]` implemented (Phase 1) · `[ ]` planned (Phase 2–4).

---

## 1. End-to-end pipeline

```
                   ┌──────────────────────────────┐
                   │        Hypothesis (YAML)     │
                   │                              │
                   │  name / statement            │
                   │  queries (multilingual)      │
                   │  sources                     │
                   │  relevance_criteria          │
                   │  languages / source_options  │
                   └──────────────┬───────────────┘
                                  │
                                  ▼
                   ┌──────────────────────────────┐
                   │   Query / Semantic Expansion │
                   │   short, distinctive terms   │
                   └──────────────┬───────────────┘
                                  │
╔═════════════════════════════════╪═════════════════════════════════════════╗
║  DATA PLANE                     │                   no demand logic here  ║
╠═════════════════════════════════╪═════════════════════════════════════════╣
║                                 ▼                                         ║
║              ┌────────────────────────────────────┐                       ║
║              │        Sources / Collectors        │                       ║
║              │                                    │                       ║
║              │  [x] Hacker News   (no key)        │                       ║
║              │  [x] Reddit        (OAuth)         │                       ║
║              │  [x] RSS / Atom    (feed URLs)     │                       ║
║              │  [ ] Stack Overflow                │                       ║
║              │  [ ] Mastodon / Bluesky            │                       ║
║              │  [ ] YouTube / public forums       │                       ║
║              └─────────────────┬──────────────────┘                       ║
║                                │  fetch(query, window)                    ║
║                                │  paging · retries · per-source isolation ║
║                                ▼                                          ║
║              ┌────────────────────────────────────┐                       ║
║              │           Normalization            │                       ║
║              │   source item → Signal             │                       ║
║              │   (nothing inferred, raw kept)     │                       ║
║              └─────────────────┬──────────────────┘                       ║
║                                ▼                                          ║
║              ┌────────────────────────────────────┐                       ║
║              │          Deduplication             │                       ║
║              │   id = source_id → url → content   │                       ║
║              │   query-independent on purpose     │                       ║
║              └─────────────────┬──────────────────┘                       ║
║                                ▼                                          ║
║              ┌────────────────────────────────────┐                       ║
║              │        Storage (SQLite)            │                       ║
║              │                                    │                       ║
║              │   signals            (raw)         │                       ║
║              │   classifications    (derived)     │                       ║
║              │   collection_state   (cursors)     │                       ║
║              └─────────────────┬──────────────────┘                       ║
╚════════════════════════════════╪══════════════════════════════════════════╝
                                 │
                    one dataset ─┤─ many hypotheses
                   (re-screening never refetches)
                                 │
╔════════════════════════════════╪══════════════════════════════════════════╗
║  INTELLIGENCE PLANE            │                        no network fetch  ║
╠════════════════════════════════╪══════════════════════════════════════════╣
║                                ▼                                          ║
║              ┌────────────────────────────────────┐                       ║
║              │  Stage 1 — Cheap Relevance Filter  │   instant · free      ║
║              │  local · deterministic · generous  │                       ║
║              │  drops: empty, short, promotional  │                       ║
║              └─────────────────┬──────────────────┘                       ║
║                                │  survivors only                          ║
║                                ▼                                          ║
║              ┌────────────────────────────────────┐                       ║
║              │  Stage 2 — TypeSafe Jev            │   250–450 ms          ║
║              │  typed choice, questions parallel  │                       ║
║              │                                    │                       ║
║              │  relevance · pain_type · intent    │                       ║
║              │  urgency · boolean flags           │                       ║
║              │  confidence < 0.5 → None           │                       ║
║              └─────────────────┬──────────────────┘                       ║
║                                │  relevant only                           ║
║                                ▼                                          ║
║              ┌────────────────────────────────────┐                       ║
║              │  Stage 3 — Generative (Gemini)     │   seconds             ║
║              │  only what Jev cannot produce      │                       ║
║              │                                    │                       ║
║              │  role · industry · language pair   │                       ║
║              │  verbatim quotes (verified)        │                       ║
║              │  never overrides Jev               │                       ║
║              └─────────────────┬──────────────────┘                       ║
║                                ▼                                          ║
║              ┌────────────────────────────────────┐                       ║
║              │          Classification            │                       ║
║              │  keyed by (signal_id, hypothesis)  │                       ║
║              │  every dimension nullable          │                       ║
║              │  stage: cheap | jev | jev+gen      │                       ║
║              └─────────────────┬──────────────────┘                       ║
║                                ▼                                          ║
║              ┌────────────────────────────────────┐                       ║
║              │            Reporting               │                       ║
║              │  pain × role × language ×          │                       ║
║              │  source × community                │                       ║
║              │  unknown is a finding, not a gap   │                       ║
║              └─────────────────┬──────────────────┘                       ║
║                                │                                          ║
║         ┌──────────────────────┴───────────────────────┐   Phase 2        ║
║         ▼                      ▼                       ▼                  ║
║  ┌─────────────┐      ┌────────────────┐      ┌────────────────┐          ║
║  │ [ ] Embed / │      │ [ ] Trend      │      │ [ ] Opportunity│          ║
║  │     Cluster │─────▶│     Detection  │─────▶│     Scoring    │          ║
║  └─────────────┘      └────────────────┘      └────────────────┘          ║
╚═══════════════════════════════╪═══════════════════════════════════════════╝
                                │
                                ▼
              ┌────────────────────────────────────┐
              │          Output surfaces           │
              │                                    │
              │  [x] CLI  table / json / csv       │
              │  [ ] Web research workspace        │
              │  [ ] Continuous radar + alerts     │
              └────────────────────────────────────┘
```

---

## 2. Package structure

```
src/demand_radar/
│
│   ┌─────────────────────────────────────────────────────────────┐
│   │  domain/            shared vocabulary                       │
│   │                     no I/O · no HTTP · no SQL · no models   │
│   │                                                             │
│   │    signal.py          Signal          one public utterance  │
│   │    hypothesis.py      Hypothesis      the research question │
│   │                       TimeWindow      absolute bounds       │
│   │    classification.py  Classification  derived dimensions    │
│   └─────────────────────────────▲───────────────────────────────┘
│                                 │  depends on
│            ┌────────────────────┴────────────────────┐
│            │                                         │
│   ┌────────┴──────────────────┐        ┌─────────────┴──────────────┐
│   │  data_plane/              │        │  intelligence/             │
│   │                           │        │                            │
│   │   collectors/   adapters  │        │   relevance.py       st. 1 │
│   │   storage/      SQLite    │        │   jev_schema.py      st. 2 │
│   │   collection.py run       │        │   jev.py             st. 2 │
│   │                           │        │   screening_schema.py st.3 │
│   │   no demand logic         │        │   providers.py       st. 3 │
│   │                           │        │   screening.py    orchestr.│
│   │                           │        │   reporting.py    rollups  │
│   └────────────▲──────────────┘        └─────────────▲──────────────┘
│                │                                     │
│                └──────────────────┬──────────────────┘
│                                   │
│                      ┌────────────┴────────────┐
│                      │  cli/        terminal   │
│                      │  config.py   env config │
│                      └─────────────────────────┘
│
└── Dependencies point inward. domain knows nothing about the planes;
    the planes know nothing about the CLI.
```

---

## 3. CLI commands against the planes

```
                         ┌──────────────────────────────┐
                         │        hypothesis.yaml       │
                         └───────────────┬──────────────┘
                                         │
      ┌──────────────────┬───────────────┼───────────────┬──────────────────┐
      │                  │               │               │                  │
      ▼                  ▼               ▼               ▼                  ▼
 ┌─────────┐      ┌────────────┐  ┌────────────┐  ┌────────────┐     ┌───────────┐
 │ sources │      │  collect   │  │   screen   │  │    run     │     │  report   │
 └────┬────┘      └─────┬──────┘  └─────┬──────┘  └─────┬──────┘     └─────┬─────┘
      │                 │               │               │                  │
      │                 ▼               ▼               ▼                  ▼
      │          ┌────────────┐  ┌────────────┐  ┌────────────┐     ┌────────────┐
      │          │ Data Plane │  │Intelligence│  │ both, then │     │ stored     │
      │          │   only     │  │   Plane    │  │  report    │     │ results    │
      │          │  (network) │  │ (no net)   │  │            │     │ only       │
      │          └────────────┘  └────────────┘  └────────────┘     └────────────┘
      ▼
 ┌─────────────────────┐
 │ collectors + their  │          demand-radar signals
 │ config requirements │          → what has been collected, any hypothesis
 └─────────────────────┘

 Options: --period (today|7d|6m|1y) · --format (table|json|csv|signals-json)
          --out · --db · --reclassify · --verbose
```

---

## 4. Invariants the diagram encodes

```
 ┌─────────────────────────────────────────────────────────────────────────┐
 │  collection ≠ intelligence     enforced by package structure            │
 │  raw ≠ derived                 separate tables; --reclassify is safe    │
 │  dedupe by content identity    not by query                             │
 │  staged by cost                each stage pays only for what passed     │
 │  uncertainty preserved         NULL reads back as None, never False     │
 │  quotes verified verbatim      paraphrase cannot enter the VoC library  │
 │  historical correctness        window resolved once, filtered locally   │
 │  failures isolated             one dead source never sinks a run        │
 └─────────────────────────────────────────────────────────────────────────┘
```
