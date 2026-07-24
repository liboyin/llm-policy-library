pip --no-cache-dir install -e .

# Install CLI AI coding tools
curl -fsSL https://chatgpt.com/codex/install.sh | bash
curl -fsSL https://claude.ai/install.sh | bash
curl -fsSL https://antigravity.google/cli/install.sh | bash

# ~/.claude and ~/.claude/plugins are created as root before installed_plugins.json is mounted as vscode
sudo chown vscode:vscode ~/.claude ~/.claude/plugins
