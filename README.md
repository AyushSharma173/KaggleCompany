# Kaggle Company

Autonomous multi-agent system for competing on Kaggle. AI agents operate as employees of a company — scouting competitions, running experiments, writing notebooks, and submitting predictions — all reporting to a human CEO via Slack.

## Quick Start

1. Copy `.env.example` to `.env` and fill in your API keys
2. Install: `pip install -e ".[dev]"`
3. Run: `python -m src.main`

## Docker

```bash
docker-compose up -d
```

## Architecture

- **VP Agent**: Portfolio manager, competition selector, CEO liaison
- **Worker Agents**: Competition specialists, ML engineers
- **Subagents**: Ephemeral task executors
- **Consolidation Agent**: Reviews experiments, updates strategy library

All agents communicate via Slack and are governed by deterministic budget controls.


run this whenever you edit stuff:
docker-compose down && docker-compose build && docker-compose up -d
Even Better:
docker-compose down && docker-compose build --no-cache && docker-compose up -d 

followed by checking logs:
docker-compose logs -f

Stop container with:
docker-compose down