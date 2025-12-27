# Contributing

Thanks for your interest in contributing to **Mirror Maestro**!

## Ways to contribute

- **Bug reports**: file an issue with clear reproduction steps.
- **Feature requests**: describe the problem and the desired behavior.
- **Code contributions**: fixes, features, docs, tests, and refactors are welcome.

## Development setup

### Prerequisites

- Python **3.11+**
- (Optional) Docker / Docker Compose for running the app in containers

### Local setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

### Running tests

```bash
pytest
```

## Project conventions

- **Keep changes focused**: one bug/feature per PR when possible.
- **Add/adjust tests** for behavior changes.
- **Avoid committing secrets**:
  - never commit `.env`
  - never commit databases or encryption keys (anything under `data/`)
  - never paste real GitLab tokens in issues, logs, or screenshots

## Pull requests

When opening a PR:

- include a short summary of the change and motivation
- include a test plan (commands run + what you verified)
- link the related issue, if applicable

## Reporting security issues

Please do **not** open public issues for security vulnerabilities.
See `SECURITY.md` for how to report them responsibly.
