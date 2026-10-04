# Workorder2data

Convert raw CMMS maintenance text (work order descriptions, technician comments, work reports) into structured records following **ISO 14224:2016**.

**Status: early development.** The converter (`w2d convert` / `w2d batch`), the synthetic test-data generator and the evaluation harness are implemented and unit-tested with a fake model; they run on the Anthropic API, the Claude Code CLI (no key) or local Ollama models; early trials only, no real work orders yet.

- Scope and principles: [IDEA.md](IDEA.md)
- Using the converter: [docs/usage.md](docs/usage.md)
- Synthetic data and evaluation: [docs/synthetic-data.md](docs/synthetic-data.md)
- Output schema: [docs/schema.md](docs/schema.md)
- Phased plan: [docs/plan.md](docs/plan.md)
- Contributor/agent conventions: [CLAUDE.md](CLAUDE.md)

## ISO 14224 is not included
The standard is copyrighted. This repository does not contain it or any tables transcribed from it. To use the tool you build `taxonomy/*.csv` from your own licensed copy; see [taxonomy/README.md](taxonomy/README.md). A fictional example taxonomy in `tests/fixtures/taxonomy/` lets the tests run without it.

## Development
```
uv sync
uv run pytest
```

## License
MIT — see [LICENSE](LICENSE). The license covers this repository's code and docs only, not ISO 14224.
