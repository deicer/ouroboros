FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl gnupg \
    && rm -rf /var/lib/apt/lists/*

# Node.js 22 (for OpenCode CLI — needed by opencode_edit tool)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# OpenCode CLI
RUN curl -fsSL https://opencode.ai/install | bash \
    && ln -sf /root/.opencode/bin/opencode /usr/local/bin/opencode \
    && opencode --version

COPY . .

# Git config for local commits
RUN git config --global user.email "e2e@test" \
    && git config --global user.name "E2E Test" \
    && git config --global init.defaultBranch main

CMD ["pytest", "tests/e2e/", "-v", "--tb=short", "-x"]
