# pip --no-cache-dir install -e .

# Install Google Antigravity CLI
curl -fsSL https://antigravity.google/cli/install.sh | bash

# devcontainer-features/claude-code installs as root, so ~/.claude and ~/.claude/plugins are owned by root
sudo chown vscode:vscode ~/.claude ~/.claude/plugins
