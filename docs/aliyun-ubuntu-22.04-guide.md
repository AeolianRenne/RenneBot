# RenneBot on Alibaba Cloud Ubuntu 22.04

This guide deploys the source checkout from this repository to one Ubuntu 22.04
ECS instance. It keeps QQ credentials in AstrBot's persistent data directory,
AI credentials in a server-only file, and allowlists in SQLite.

## 1. Prepare the instance

Use a fixed public IPv4 address. A 2 vCPU, 4 GB RAM instance is a practical
starting point because the first deployment builds the complete AstrBot source.

In the Alibaba Cloud security group:

- Allow TCP 22 only from the administrator's public IP.
- Do **not** open TCP 6185 or 6199. RenneBot binds both ports to localhost and
  the dashboard is accessed through an SSH tunnel.

Log in with the intended deploy user, then install Docker Engine and Compose:

```bash
sudo apt update
sudo apt install -y ca-certificates curl git

sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
exit
```

Log in again and verify:

```bash
docker run hello-world
docker compose version
```

## 2. Grant read-only repository access

On the server, create a dedicated SSH key:

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
ssh-keygen -t ed25519 -f ~/.ssh/rennebot_deploy -C "rennebot-server"
cat ~/.ssh/rennebot_deploy.pub
```

Add this public key in GitHub under **Settings → Deploy keys** for both
`AeolianRenne/RenneBot` and `AeolianRenne/astrbot-plugin-rennebot`. Leave write
access disabled in both repositories. Add the following entry to `~/.ssh/config`:

```sshconfig
Host github.com
  IdentityFile ~/.ssh/rennebot_deploy
  IdentitiesOnly yes
```

Then verify the connection:

```bash
chmod 600 ~/.ssh/config
ssh -T git@github.com
```

## 3. Clone and start the service

Run the bootstrap script once. It clones the source to `/home/admin/RenneBot`,
creates `/opt/rennebot/runtime`, and copies a server-only environment template.

```bash
git clone git@github.com:AeolianRenne/RenneBot.git /tmp/rennebot-bootstrap
bash /tmp/rennebot-bootstrap/scripts/server-bootstrap-rennebot.sh \
  git@github.com:AeolianRenne/RenneBot.git
```

Edit the runtime-only API configuration:

```bash
nano /opt/rennebot/runtime/bot.env
```

Set only the AI endpoint values:

```dotenv
OPENAI_API_BASE=https://your-openai-compatible-endpoint/v1
OPENAI_API_KEY=your-api-key
OPENAI_MODEL=your-model-name
AI_TIMEOUT_SECONDS=30
```

If the Alibaba Cloud Docker accelerator cannot pull the Python official image,
add this non-secret build setting to the same file:

```
PYTHON_BASE_IMAGE=m.daocloud.io/docker.io/library/python:3.12-slim
```

Start the source build and container:

```bash
cd /home/admin/RenneBot
BOT_ENV_FILE=/opt/rennebot/runtime/bot.env RUNTIME_DIR=/opt/rennebot/runtime \
  docker compose --env-file /opt/rennebot/runtime/bot.env \
  -f compose.rennebot.yml up -d --build
```

The first build can take several minutes. Check it with:

```bash
BOT_ENV_FILE=/opt/rennebot/runtime/bot.env RUNTIME_DIR=/opt/rennebot/runtime \
  docker compose -f compose.rennebot.yml ps
```

## 4. Configure QQ Official Bot

On the local Windows machine, open an SSH tunnel and keep the command running:

```powershell
ssh -N -L 6185:127.0.0.1:6185 <deploy-user>@<server-public-ip>
```

Open `http://127.0.0.1:6185` in a browser. Retrieve the initial AstrBot password
from server logs, sign in, and change it immediately:

```bash
cd /home/admin/RenneBot
BOT_ENV_FILE=/opt/rennebot/runtime/bot.env RUNTIME_DIR=/opt/rennebot/runtime \
  docker compose -f compose.rennebot.yml logs --tail 100
```

In AstrBot:

1. Create and enable a **QQ Official Bot (WebSocket)** adapter.
2. Enter the AppID and AppSecret from the QQ Bot platform.
3. Disable AstrBot's default LLM chat. RenneBot's plugin handles all authorized
   AI requests itself.

In the QQ Bot platform:

1. Add the ECS public IPv4 address to the IP allowlist.
2. Configure the sandbox or production private chat and QQ group permissions.
3. Add the bot to the intended test group.

## 5. Initialize database-backed access control

The first run intentionally has no administrator or AI allowlist. In a private
chat, send the bot `/renne-id` to receive your platform user ID. Then use this
ID once to seed the SQLite administrator list:

```bash
cd /home/admin/RenneBot
RENNEBOT_BOOTSTRAP_ADMIN_IDS='<your-platform-user-id>' \
BOT_ENV_FILE=/opt/rennebot/runtime/bot.env RUNTIME_DIR=/opt/rennebot/runtime \
  docker compose -f compose.rennebot.yml up -d --force-recreate
```

Immediately recreate the container without that one-time environment variable:

```bash
BOT_ENV_FILE=/opt/rennebot/runtime/bot.env RUNTIME_DIR=/opt/rennebot/runtime \
  docker compose -f compose.rennebot.yml up -d --force-recreate
```

As an administrator, send private configuration commands:

```text
/renne-config show
/renne-config ai-users set <user-id-1,user-id-2>
/renne-config ai-groups set <group-id-1,group-id-2>
/renne-config admins set <admin-id-1,admin-id-2>
```

Get a group's platform ID with `@机器人 /renne-id`. Group game-ID commands work
in any group where the bot is available; only AI requires an `ai-groups` entry.

## 6. Recover configuration without a QQ administrator

The persistent database is:

```text
/opt/rennebot/runtime/astrbot-data/plugin_data/qq_game_registry/rennebot.sqlite3
```

To recover from every administrator ID being removed, stop the container, create
a backup, update SQLite locally on the server, and start it again:

```bash
cd /home/admin/RenneBot
database=/opt/rennebot/runtime/astrbot-data/plugin_data/qq_game_registry/rennebot.sqlite3

BOT_ENV_FILE=/opt/rennebot/runtime/bot.env RUNTIME_DIR=/opt/rennebot/runtime \
  docker compose -f compose.rennebot.yml stop
cp -a "$database" "/opt/rennebot/runtime/backups/rennebot-manual-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"

python3 scripts/rennebot-db.py --database "$database" set admins <new-admin-id>
python3 scripts/rennebot-db.py --database "$database" set ai-users <user-id>
python3 scripts/rennebot-db.py --database "$database" show

BOT_ENV_FILE=/opt/rennebot/runtime/bot.env RUNTIME_DIR=/opt/rennebot/runtime \
  docker compose -f compose.rennebot.yml start
```

`set-json <key> <json>` writes any future plugin setting. Treat SSH access and
the database file as privileged access.

## 7. Deploy updates from Windows

Commit and push the local source changes first. Then run from the local checkout:

```powershell
cd C:\Users\ASUS\Documents\QQAgent\RenneBot\RenneBot
$env:RENNEBOT_DEPLOY_HOST = "<deploy-user>@<server-public-ip>"
.\deploy.ps1
```

The script runs plugin tests, pushes local `master`, fetches the exact commit on
the server, backs up SQLite, and recreates the service. Plugin-only changes are
read from the mounted source checkout and do not rebuild the AstrBot image.
To deploy an earlier commit already present in remote history:

```powershell
.\deploy.ps1 -Commit <full-40-character-sha> -NoPush
```
