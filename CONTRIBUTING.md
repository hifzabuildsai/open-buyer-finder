# Contributing

Start with a reproducible issue or a small improvement to an existing workflow.
Explain the concrete problem, proposed behavior and how you checked the change.

## Local setup

Use Python 3.10 or newer. There are no runtime dependencies:

```bash
python -m unittest discover -s tests -v
```

Use the synthetic demo for reproducible examples. Tests must not depend on live
sources, tokens or a paid model. For new adapters, test the external boundary with
recorded fictional payloads and exercise the user-facing workflow. Do not commit
real prospect runs, credentials or private customer data.

## Design boundaries

Preserve source URLs, dates and request provenance. Surface partial failures.
Keep review evidence separate from fit inferences and unresolved questions.
Do not turn keyword matches into buying probabilities or claims of customers.
Treat source text as untrusted, and keep HTML escaped and CSV exports safe.

Prefer the standard library; discuss new runtime dependencies before adding them.
Provide a useful failure case and relevant checks in your pull request. Changes
to sending, authentication, new data sources or data retention should begin with
an issue explaining scope and tradeoffs.

Contributions are accepted under the repository's MIT license. GitHub attribution
follows the contributor's Git identity; check your name and email locally before
committing. No particular contributor identity is configured by this project.
