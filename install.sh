#!/usr/bin/env sh
set -eu

REPO_RAW_URL="${GATREST_RAW_URL:-https://raw.githubusercontent.com/blackfyredev/gatrest/main/gatrest.py}"
INSTALL_PATH="${GATREST_INSTALL_PATH:-/usr/local/bin/gatrest}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run this installer with sudo so it can write to /usr/local/bin." >&2
  echo "Example: curl -fsSL https://raw.githubusercontent.com/blackfyredev/gatrest/main/install.sh | sudo sh" >&2
  exit 1
fi

tmp_file="$(mktemp)"
trap 'rm -f "$tmp_file"' EXIT

if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$REPO_RAW_URL" -o "$tmp_file"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "$tmp_file" "$REPO_RAW_URL"
else
  echo "curl or wget is required to install gatrest." >&2
  exit 1
fi

install -m 0755 "$tmp_file" "$INSTALL_PATH"
echo "Installed gatrest to $INSTALL_PATH"
echo "Run: gatrest --help"
