# ClawBench Domain Suite

`tasks-public/` is the small public Core v1 set. `tasks-domain/` is the
coverage scaffold for the larger proof corpus: the domains served by most
agent SaaS products, expressed as deterministic benchmark work.

The claim this suite is meant to support is:

> A capable model plus a general agent harness plus the right plugins can
> cover the task domains that most agent SaaS products sell.

This is intentionally not a clone of vendor products. It is a taxonomy of
jobs, state transitions, and verifier contracts.

## Domains

| Domain | Representative jobs | Required plugin surface | Verification style |
|---|---|---|---|
| CRM | lead enrichment, account updates, meeting notes to opportunities | browser, CRM API, docs, search | API state assertions, fixture diffs |
| Support | ticket triage, macro draft, escalation, refund lookup | browser/API, knowledge base, email | ticket state, cited evidence, policy checks |
| Email and calendar | thread summarization, scheduling, follow-ups | mail, calendar, contacts, memory | event state, draft content, no-duplicate checks |
| Docs, sheets, slides | spreadsheet cleanup, deck edits, document redaction | file, office docs, charting | structural file assertions, rendered diffs |
| Project management | issue grooming, sprint updates, dependency tracking | PM API, repo, docs, notifications | issue state, links, blocked/unblocked status |
| Finance ops | invoice reconciliation, expense coding, budget variance | spreadsheets, accounting API, OCR | ledger deltas, numeric tolerances, audit trail |
| Data analytics | SQL, dashboard explanation, ETL patch, anomaly report | database, notebooks, BI API | query results, chart spec, report content |
| Security admin | access review, incident timeline, secret rotation plan | identity, logs, repo, policy docs | policy state, log-derived evidence, refusal gates |
| Ecommerce ops | catalog updates, order exception handling, promo QA | storefront API, spreadsheet, browser | product state, order workflow, price checks |
| Devtools | repo migration, CI fix, release note, dependency update | shell, git, code, package registry | test pass, diff assertions, changelog checks |
| Research | web evidence, citation synthesis, source contradiction | browser, web search, docs | citation verifier, no-fabrication checks |
| Personal ops | travel, household planning, health/wellness admin | calendar, browser, memory, docs | constraint satisfaction, state updates |

## Proof Standard

Each domain task should declare:

- `domain`: one of the domains above
- `job`: the user-facing job being covered
- `saas_equivalents`: examples of products whose core workflow overlaps
- `plugin_requirements`: tool families and state surfaces needed
- `deterministic_floor`: the verifier that must pass before any judge score
- `holdout_variant_policy`: how private variants are generated
- `ablation_axis`: which plugins or harness capabilities the task tests

## Minimum Bar

For a credible first domain release:

- 12 domains
- 5 task templates per domain
- 3 private variants per template
- 3 runs per configuration
- at least 4 configuration classes:
  - model only
  - model plus harness
  - model plus harness plus core plugins
  - model plus harness plus domain plugins

That yields 60 public templates and 180 private variants before repetitions.
The public templates explain coverage; the private variants carry the proof.
