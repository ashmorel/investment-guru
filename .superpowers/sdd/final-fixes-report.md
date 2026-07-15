# Decision Cockpit final-review fixes

Date: 2026-07-15

## Implemented

- Strict decision-brief grounding now binds news evidence reference, symbol, headline, source, and URL to the canonical context row; candidate symbol, name, market, and instrument type to its canonical candidate; and payload `data_as_of` to context `data_as_of`.
- Grounding failures continue through the existing single correction request. A second invalid response raises `LLMError`; regression coverage proves that neither a report nor usage row is persisted.
- `DecisionNewsItem.url` remains a string but accepts only absolute HTTP(S) URLs.
- Decision payload contracts cap candidates at five, require unique candidate symbols, require unique material-news evidence references, and require at least one evidence reference per candidate.
- The frontend renders links only for safe HTTP(S) URLs. Unsafe legacy values render the headline as plain text in both evidence details and the material-news list.
- The app shell uses top/flow navigation on small screens and restores the existing fixed-width sidebar from `md`; main content has `min-w-0` and mobile padding.
- Cockpit subsections use `h3` beneath the cockpit `h2`; accessibility coverage remains active.

## TDD evidence

- RED backend focused run: `8 failed, 24 passed`, covering unsafe URLs, limits, canonical news/candidate metadata, and timestamp grounding.
- RED frontend focused run: `3 failed, 13 passed`, covering unsafe-link fallback, nested heading levels, and responsive class contracts.
- GREEN backend focused run: `32 passed`.
- GREEN frontend focused run: `16 passed`.

## Full verification

- Backend Ruff: `All checks passed!`
- Backend pytest: `436 passed in 147.35s`
- Frontend `npm run check`: TypeScript, lint, `147 passed` across 20 files, and production build all completed successfully.
- Existing non-blocking frontend warning remains: `react(only-export-components)` in `NewsPanel.tsx`.
- Existing jsdom canvas diagnostics remain during Vitest; they do not fail the suite.

## Browser verification

- Real Chromium via Playwright CLI, deterministic authenticated `/api/*` fixtures.
- Viewport: `390 × 844`.
- Rendered authenticated dashboard and navigation.
- Metrics: `window.innerWidth=390`, `document.documentElement.scrollWidth=390`, `document.body.scrollWidth=390`; `noOverflow=true`.

## Nonblocking note

- Candidate-provider reads remain sequential in this fix wave as requested. Parallelizing independent reads is a future performance suggestion, not a correctness blocker.
