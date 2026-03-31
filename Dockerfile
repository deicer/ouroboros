FROM python:3.12-slim

# System deps: git, curl, gnupg, tini (signal handling)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl gnupg tini \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright browser (for browse_page/browser_action tools)
RUN playwright install --with-deps chromium

# Create ouroboros OS user (bypassPermissions is blocked for root)
RUN useradd -m -s /bin/bash ouroboros

COPY . .
RUN git config --global --add safe.directory /app
RUN chown -R ouroboros:ouroboros /app

ENTRYPOINT ["/usr/bin/tini", "--"]
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
  CMD python -c "import ouroboros; print('ok')" || exit 1
CMD ["python", "launcher.py"]
