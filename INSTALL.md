# Installation Guide

Deploy Ouro on a VPS using Claude Code.

---

## VPS Requirements

Ouro runs Python + Chromium (Playwright) + Claude Code CLI (Node.js) inside Docker. Chromium is the biggest memory consumer at 800 MB–1.5 GB per browser tab.

| Tier | RAM | vCPU | Disk | Monthly cost (approx) |
|------|-----|------|------|-----------------------|
| **Minimum** | 4 GB | 2 | 40 GB | ~$7 (Hetzner CX32), ~$24 (DigitalOcean) |
| **Recommended** | 8 GB | 2 | 80 GB | ~$14 (Hetzner CX42), ~$48 (DigitalOcean) |

**Why these specs:**

| Component | RAM usage |
|-----------|-----------|
| Chromium (Playwright) | 800 MB – 1.5 GB per tab |
| Claude Code CLI (Node.js) | 300 – 500 MB |
| Python agent + supervisor | ~200 MB |
| Docker + OS overhead | ~200 MB |

Ubuntu 22.04+ or Debian 12+ recommended. Any Linux with Docker support works.

---

## Prerequisites

Before starting, you need:

1. **A VPS** with SSH access (see specs above)
2. **A GitHub fork** of this repository — Ouro pushes commits to its own repo, so it needs a fork
3. **API keys** — see the table below
4. **Claude Code** with SSH access to the VPS

### Required API Keys

| Key | Where to get it |
|-----|-----------------|
| `OPENROUTER_API_KEY` | [openrouter.ai/keys](https://openrouter.ai/keys) — create account, add credits, generate key |
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) on Telegram — `/newbot`, copy the token |
| `GITHUB_TOKEN` | [github.com/settings/tokens](https://github.com/settings/personal-access-tokens/new) — fine-grained token with **Contents: Read and write** on your fork |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com/settings/keys) — powers Claude Code CLI (code editing path) |
| `COMPOSIO_API_KEY` | [composio.dev](https://composio.dev) — external app integrations (Gmail, Slack, etc.) |

### Optional API Keys

| Key | Purpose |
|-----|---------|
| `OPENAI_API_KEY` | Enables the `web_search` tool |
| `TOTAL_BUDGET` | Fallback spending limit in USD (if your OpenRouter key has no limit) |

---

## Installation Steps (for Claude Code)

These are step-by-step instructions. If you're using Claude Code to deploy, give it SSH access to the VPS and ask it to follow this guide.

### 1. Install Docker

```bash
# Install Docker Engine
curl -fsSL https://get.docker.com | sh

# Verify
docker --version
docker compose version
```

### 2. Clone the Fork

```bash
git clone https://github.com/<GITHUB_USER>/ouro.git
cd ouro
```

Replace `<GITHUB_USER>` with the GitHub username that owns the fork.

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and fill in all required values:

```env
OPENROUTER_API_KEY=sk-or-...
TELEGRAM_BOT_TOKEN=123456:ABC...
GITHUB_TOKEN=github_pat_...
GITHUB_USER=<your-github-username>
GITHUB_REPO=ouro
ANTHROPIC_API_KEY=sk-ant-...
COMPOSIO_API_KEY=...
```

See `.env.example` for optional model and infrastructure settings.

### 4. Adjust Resource Limits

Edit `docker-compose.yml` to match your VPS. The defaults (1 CPU, 2 GB) are too tight for Playwright browser use.

**For 4 GB VPS:**
```yaml
cpus: 2.0
mem_limit: 3g
```

**For 8 GB VPS:**
```yaml
cpus: 2.0
mem_limit: 6g
```

### 5. Build and Launch

```bash
docker compose up -d --build
```

First build takes ~5 minutes (installs Playwright/Chromium, pip dependencies, Claude Code CLI, GitHub CLI).

### 6. Verify

```bash
docker compose ps        # Should show "running"
docker compose logs -f   # Watch startup logs
```

Look for:
- `First-run initialization (Bible section 18)` — initial file setup
- `Supervisor started` — ready for messages

### 7. Connect via Telegram

Open the Telegram bot you created and send any message. The first person to message becomes the **owner**. Ouro will check its subsystems and introduce itself.

---

## What Happens on First Run

The launcher (`launcher.py`) automatically handles first-run initialization:

1. Validates all API keys and connections
2. Initializes git — creates a dev branch from `main`, pushes to your fork
3. Ensures `improvements-log/` directory and agent skills are set up
4. Pre-installs the `find-skills` agent skill
5. Commits and pushes initial files
6. Seeds budget from OpenRouter API
7. Starts worker processes and begins Telegram polling
8. Waits for first message to register owner

No manual initialization needed beyond getting Docker running with the right `.env`.

---

## Operations

| Action | Command |
|--------|---------|
| Check status | `docker compose ps` |
| View logs | `docker compose logs -f` |
| Stop | `docker compose down` |
| Start | `docker compose up -d` |
| Rebuild | `docker compose up -d --build` |

The container auto-restarts on failure. All state persists in a Docker volume (`ouro-data`) — survives restarts and rebuilds.

Telegram commands: `/status`, `/restart`, `/panic`, `/break`, `/budget`, `/rollback`.

---

## Troubleshooting

**Container exits immediately:**
Check logs with `docker compose logs`. Usually a missing or invalid API key in `.env`.

**Out of memory (OOM killed):**
Increase `mem_limit` in `docker-compose.yml`. Chromium needs 1+ GB. Check with `docker inspect ouro-ouro-1 | grep OOMKilled`.

**Playwright/browser failures:**
Ensure `mem_limit` is at least 3 GB. Chromium won't launch under heavy memory pressure.

**Git push failures:**
Verify `GITHUB_TOKEN` has write access to the fork and `GITHUB_USER` matches the fork owner.

**"Bootstrap failed" on startup:**
Usually a network issue fetching from GitHub. Check connectivity and that `GITHUB_TOKEN` is valid.
