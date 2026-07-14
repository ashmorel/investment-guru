# Decision Cockpit and Stock Discovery — Design Spec

_Date: 2026-07-14_

## Goal

Make Investment Guru faster to read and act on by replacing the dashboard's fragmented news, signals, and advice surfaces with one on-demand, saved **Decision Brief**. The brief leads with clear verdicts for current holdings, attaches the evidence behind each verdict, highlights only decision-relevant news, and recommends a small grounded shortlist of stocks or ETFs the user may consider holding.

The user chose an action-first "Decision Cockpit" layout, on-demand generation, stocks plus ETFs, and "consider holding" language with low/medium/high conviction. Automatic or event-triggered generation may be added later, but is not part of this version.

## Context

The app already has most of the required inputs, but they are split across surfaces:

- The dashboard shows portfolio cards, a saved Guru take, deterministic attention signals, and a long grouped news panel.
- Portfolio reviews already return per-position `hold | increase | reduce | exit` verdicts.
- Per-stock news summaries provide sentiment and key points.
- The Guru take supplies broad risks and ideas, but it is not a grounded discovery pipeline.
- Sector rotation, investor profile, valuations, signals, quotes, history, fundamentals, and news already have service boundaries that can be reused.

The new capability is a coherent cross-portfolio decision snapshot plus discovery of eligible instruments outside current holdings.

## Product decisions

1. **Action first.** Holding verdicts appear above news, portfolio context, and discovery.
2. **On demand.** The dashboard loads the latest saved brief cheaply. The LLM runs only when the user presses **Generate new brief**.
3. **One coherent report.** Holding actions, material evidence, portfolio observations, and discovery ideas are generated from the same timestamped context.
4. **Grounded discovery.** Code assembles and scores a bounded candidate universe. The LLM may rank and explain only supplied candidates.
5. **Stocks plus ETFs.** An ETF may be recommended when it expresses an attractive theme or diversification need more safely than a single company.
6. **Consider holding.** New ideas use research-oriented `consider` language and low/medium/high conviction, not trade instructions.
7. **No automatic portfolio changes.** A candidate may be added to a watchlist through the existing position workflow; it is never automatically bought or added to a real portfolio.

## User experience

### 1. Header and freshness

The dashboard header contains **Generate new brief**, the latest brief's generation time, and its underlying data timestamp. Regeneration creates a new saved report and makes it the displayed latest report; earlier reports remain stored.

If no report exists, the dashboard explains what the brief contains and invites the user to generate one. Existing portfolio summary cards remain accessible but no longer dominate the decision flow.

### 2. Actions across holdings

The primary section aggregates holdings across all of the user's real portfolios and groups verdicts into:

- **Act:** `exit`, `reduce`, and `increase`.
- **Hold:** collapsed by default.
- **Data incomplete:** held instruments for which current evidence is insufficient.

Each row shows symbol, action, conviction, and a one-sentence rationale. An accessible **View evidence** disclosure shows:

- deterministic signals supporting the verdict;
- material news headlines with source and URL;
- relevant exposure or concentration context; and
- what would change the view.

Uncertainty must not be silently represented as `hold`.

### 3. News that matters

The prominent news section contains only headlines that affect, or may affect, a holding verdict. Items are labelled:

- **Material:** directly changes or strongly supports an action;
- **Watch:** may change the view if confirmed; or
- **Context:** relevant background without an immediate action implication.

The existing complete headline lists remain available lower on the dashboard and on position details. This preserves the reading surface without forcing all news above the fold.

### 4. Portfolio context

A compact panel explains cross-portfolio factors that influenced the report: concentration, sector/theme exposure, diversification gaps, investor risk/horizon fit, and unavailable inputs.

### 5. Ideas to consider holding

The brief displays at most five stocks or ETFs that are not currently held. Each candidate card contains:

- symbol, name, instrument type, and market;
- `consider` and low/medium/high conviction;
- why it surfaced;
- how it fits the current portfolio and investor profile;
- its principal risk;
- what evidence or event to watch next; and
- an action that uses the existing workflow to add it to a selected watchlist.

The UI provides no direct trade execution.

### Responsive and accessible behavior

Desktop uses the approved action-first hierarchy. Mobile renders the same content in one column and preserves the order: actions, material news, portfolio context, then ideas. Controls use semantic buttons and disclosures, verdicts have text labels in addition to colour, focus order follows reading order, and new UI receives `vitest-axe` coverage.

## Architecture

The implementation is divided into small units with explicit inputs and outputs.

### Recommendation catalogue

`app/services/recommendations/catalog.py` reads a version-controlled catalogue of supported major US, UK, and HK stocks and broad/thematic ETFs. Catalogue entries contain symbol, name, market, currency, instrument type, sector, and theme tags.

The catalogue is bounded and reviewable. It avoids dependence on a paid whole-market screener and prevents model-memory discovery. A validation test rejects missing required fields, duplicate canonical symbols, unsupported markets, and invalid instrument types.

### Candidate assembly

`app/services/recommendations/candidates.py` combines:

- the curated catalogue;
- instruments in the user's watchlists; and
- profile sector interests mapped to catalogue sector/theme tags.

It removes instruments already held in any real portfolio and canonicalizes symbols using the app's existing market conventions. Watchlist membership is a source signal, not an automatic recommendation.

### Deterministic scoring

`app/services/recommendations/scoring.py` obtains current inputs through the existing quote, history, fundamentals, news, signals, valuation, and group/exposure abstractions. It scores and filters candidates using explicit factors:

- momentum and deterministic signals;
- available fundamental and valuation inputs;
- recent news activity and catalogue theme tags;
- diversification fit against current holdings and groups;
- investor profile fit; and
- stale or missing-data penalties.

Scores are for shortlist construction, not user-facing price targets. Factor values and availability accompany every shortlisted candidate. A candidate without enough current evidence is omitted rather than guessed. The shortlist passed to the LLM is bounded to control latency and token use.

### Decision context

`app/services/guru/decision_context.py` assembles one user-scoped context containing:

- aggregated positions and valuations across all real portfolios;
- current deterministic signals;
- recent deduplicated headlines and their URLs;
- group/sector exposure and concentration observations;
- investor profile; and
- the verified candidate shortlist with scores, factors, and availability.

Duplicate instruments held in multiple portfolios are combined for exposure analysis while retaining source portfolio identifiers for evidence display. Every input degrades independently and is recorded in an availability block.

### Guru generation

`GuruService.generate_decision_brief` follows the existing advice-generation pattern:

1. check the user's daily budget;
2. acquire a per-user decision-brief generation lock;
3. build the decision context;
4. make a structured advice-model call;
5. validate references and symbols against the supplied context;
6. retry once with a constrained correction prompt on unknown symbols or invalid evidence references;
7. persist only a fully valid encrypted report; and
8. record usage under a distinct `decision` mode.

The instruction requires the model to reason only from supplied context, avoid invented figures, distinguish missing data from a hold verdict, use directional language, and include the standard disclaimer.

## Structured payload

New Pydantic schemas named `DecisionBriefPayload`, `HoldingDecision`, `DecisionNewsItem`, and `CandidateIdea` define this contract:

```text
DecisionBriefPayload
  summary: str
  holdings: HoldingDecision[]
  material_news: DecisionNewsItem[]
  portfolio_observations: str[]
  candidates: CandidateIdea[]
  unavailable_inputs: str[]
  data_as_of: datetime
  disclaimer: str

HoldingDecision
  symbol: str
  action: hold | increase | reduce | exit | data_incomplete
  conviction: low | med | high | null
  rationale: str
  evidence_refs: str[]
  change_conditions: str[]

DecisionNewsItem
  evidence_ref: str
  symbol: str
  importance: material | watch | context
  headline: str
  source: str
  url: str
  impact: str

CandidateIdea
  symbol: str
  name: str
  instrument_type: stock | etf
  market: US | UK | HK
  action: consider
  conviction: low | med | high
  why_surfaced: str
  portfolio_fit: str
  principal_risk: str
  watch_next: str[]
  evidence_refs: str[]
```

Evidence references are stable identifiers created by the context builder, not free-form model citations. Backend validation confirms that every symbol and evidence reference exists in the supplied context.

## Persistence and API

The feature reuses `GuruReport` with `kind="decision"` and its existing encrypted JSON payload. The current `kind` column is sufficiently wide, so no database migration or new user-data table is required.

Endpoints in `app/api/guru.py`:

- `POST /api/guru/decision-brief` generates, validates, saves, and returns a new report.
- `GET /api/guru/decision-brief/latest` returns the user's latest saved decision report or `null` when none exists.

Both endpoints are authenticated and user-scoped. The frontend uses distinct TanStack Query keys and invalidates the latest-report query after successful generation.

The existing portfolio-review, Guru take, digest, chat, per-stock news-summary, and sector-rotation endpoints remain intact. The new dashboard composes their important underlying facts into a coherent report rather than deleting specialized workflows.

## Error handling and degraded data

Generation uses the established mappings:

- budget exhausted → `429 budget_exhausted`;
- generation already running → `409 generation_in_progress`;
- LLM not configured → `503 llm_unconfigured`;
- provider, correction, or schema failure → `502 llm_error`.

Nothing is persisted on failure. Provider details and secrets are scrubbed from errors.

Market-data failures are isolated per instrument and input type:

- a discovery candidate with insufficient current evidence is omitted;
- a held instrument remains visible with `data_incomplete` and explicit missing inputs;
- stale cached data may be used only when labelled with its timestamp and availability state; and
- headlines and portfolio values already available in the app continue to render if decision generation fails.

Unknown candidate symbols, unsupported actions, or evidence references outside the assembled context trigger one constrained re-prompt. A second invalid result becomes `LLMError`; no partial report is saved.

## Testing

### Backend

- Catalogue schema validation, canonical-symbol uniqueness, supported market/type checks.
- Candidate assembly includes eligible watchlist/catalogue/profile sources and excludes every current holding.
- User isolation for watchlists, holdings, profile, reports, and context.
- Deterministic score ordering, factor output, stale penalties, and per-provider degradation.
- Cross-portfolio aggregation of duplicate holdings and exposure context.
- Decision-context availability block and stable evidence references.
- Structured generation success, usage recording, and encrypted persistence.
- Unknown-symbol and invalid-evidence correction retry, then clean failure.
- Budget, concurrency, unconfigured-provider, and provider/schema error mappings.
- Latest endpoint returns only the requesting user's report and returns `null` when absent.

All provider interactions use fixture-backed fakes. Async tests follow the repository's session loop-scope convention.

### Frontend

- Empty, loading, generation, saved-report, and all mapped error states.
- Action ordering and grouping; `hold` collapsed; `data_incomplete` distinct.
- Accessible evidence disclosures and external news links.
- Portfolio-context and candidate-card rendering.
- Add-to-watchlist flow uses the existing position API and never targets a real portfolio implicitly.
- Responsive single-column reading order.
- `vitest-axe` on the Decision Cockpit and expanded evidence state.
- Regression coverage for existing dashboard, news, Guru, and portfolio behavior.

### Verification

Run the repository's required backend and frontend verification suites. Production smoke checks cover authentication, latest-empty behavior, one on-demand generation, persisted reload, external news links, candidate watchlist addition, and graceful unauthenticated/error responses.

## Figma gate

Before frontend implementation, convert the approved Decision Cockpit wireframe into an implementation-ready frame in the existing Investment Guru Figma file (`0gU58wfjttdZS0NXQeEtuD`). Review desktop and mobile hierarchy, expanded evidence, empty/generating/error states, and reuse of the current design tokens. Frontend implementation begins only after that frame is approved.

## Future extension points

The generation trigger is separated from context assembly and persistence so a future scheduler or material-change trigger can call the same service without changing the report schema or dashboard. Catalogue storage can later move to an admin-managed table or external screener without changing the candidate/scoring interface.

## Out of scope

- Scheduled, automatic, or event-triggered brief generation.
- Brokerage integration or trade execution.
- An unrestricted whole-market or paid screener.
- Recommendations based on model memory.
- Price targets, position sizes, or trade amounts.
- Full-article scraping or storage.
- Automatic additions to a real portfolio or watchlist.
