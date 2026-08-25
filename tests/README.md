# Test layout

- `tests/unit/`: pure, fast tests with no real network or database.
- `tests/contract/`: shared-model and public-API compatibility tests.
- `tests/integration/`: real infrastructure boundaries in an isolated environment.
- `tests/e2e/`: a few full workflow tests.
- `tests/live/`: opt-in source smoke tests; never part of the normal local or PR suite.
- `tests/fixtures/`: versioned RSS, JSON, HTML, and model-response samples with origin notes.

