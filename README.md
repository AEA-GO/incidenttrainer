# IncidentTrainer

A browser game where you play the on-call engineer: a real server breaks, you find and fix the cause, keep people informed, and get scored.

**Status:** early development. Closed beta planned for December 2026.

## How it works
1. Start a lab: we launch a fresh, private server running a small online shop.
2. Get paged, investigate with real dashboards, logs and a terminal, and fix the real cause.
3. Get a score for the fix and for how you communicated. The server is then destroyed.

## This repo
- `web/`: website (React + TypeScript)
- `api/`: backend API (Python, FastAPI on AWS Lambda)
- `infra/`: AWS infrastructure (Terraform)
- `.github/`: CI/CD (GitHub Actions)
- `docs/`: documentation, including [local setup](docs/setup.md)

Lab content lives in a separate private repo.
