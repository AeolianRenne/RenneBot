#!/usr/bin/env bash
# Run once on the Alibaba Cloud Linux server after Docker is installed.
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <git-repository-url>" >&2
  exit 2
fi

repo_url="$1"
app_dir="/opt/rennebot/app"
runtime_dir="/opt/rennebot/runtime"

if [[ -e "$app_dir" ]]; then
  echo "$app_dir already exists; refusing to overwrite it." >&2
  exit 1
fi

sudo install -d -m 0755 "$(dirname "$app_dir")"
sudo install -d -m 0750 "$runtime_dir" "$runtime_dir/astrbot-data" "$runtime_dir/backups"
sudo chown -R "$(id -u):$(id -g)" "$(dirname "$app_dir")"
git clone "$repo_url" "$app_dir"
cp "$app_dir/.env.rennebot.example" "$runtime_dir/bot.env"
chmod 0600 "$runtime_dir/bot.env"

cat <<EOF
Bootstrap complete.
1. Edit $runtime_dir/bot.env with the OpenAI-compatible API and allowlists.
2. Configure a read-only SSH deploy key for the repository and add this user to the docker group.
3. Start: cd $app_dir && BOT_ENV_FILE=$runtime_dir/bot.env RUNTIME_DIR=$runtime_dir docker compose -f compose.rennebot.yml up -d --build
4. Tunnel the dashboard: ssh -L 6185:127.0.0.1:6185 <server>
EOF
