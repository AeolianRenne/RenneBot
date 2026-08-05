# RenneBot deployment

This repository is an AstrBot source checkout with a versioned custom plugin in
`rennebot_plugin/qq_game_registry`. The plugin source is mounted read-only into
AstrBot at runtime. Its SQLite database lives in AstrBot's persistent plugin data
directory and is not part of Git.

## Local development

```powershell
cd C:\Users\ASUS\Documents\QQAgent\RenneBot\RenneBot
uv sync
uv run pytest tests/rennebot
uv run ruff check rennebot_plugin tests/rennebot
Copy-Item .env.rennebot.example .env.rennebot
docker compose -f compose.rennebot.yml up -d --build
```

The dashboard is bound to `127.0.0.1:6185`. After the container starts, sign in,
change the generated AstrBot password, and create a **QQ Official Bot
(WebSocket)** adapter. Enter the QQ AppID and AppSecret in the dashboard; AstrBot
persists that configuration in `runtime/astrbot-data`. Keep AstrBot's default LLM
chat disabled because RenneBot handles the allowed AI calls itself.

## QQ behavior

The plugin intercepts QQ Official messages before AstrBot's normal LLM flow.

- A private text message invokes AI only when its sender ID is listed in
  `AI_PRIVATE_USER_IDS`.
- A group request must be `@机器人 /ai <问题>` and its group ID must be in
  `AI_GROUP_IDS`.
- All groups where the bot is present can use the non-AI commands below:
  - `@机器人 /记录游戏id <游戏名> <数字ID>`
  - `@机器人 /查询群友id <游戏名>`
  - `@机器人 /删除游戏id <游戏名> [QQ号]`
- A member may delete only their own record. IDs in `BOT_ADMIN_USER_IDS` may
  delete another member's record by supplying that member's QQ ID.

Use `/renne-id` to obtain the platform IDs for the comma-separated allowlists:
send it in a private chat to see your user ID, or send `@机器人 /renne-id` in a
group to see the group and sender IDs. Every AI request is one-shot and no
conversation history is written by this plugin.

## Alibaba Cloud server

Run `scripts/server-bootstrap-rennebot.sh <repository-url>` once on the Linux
server after Docker is installed and a read-only Git deploy key is configured.
Then edit `/opt/rennebot/runtime/bot.env` and start the Compose service as shown
by the script.

Add the server's fixed public IP to the QQ Bot platform IP allowlist and grant
the required sandbox/group/private-chat permissions. Do not open Alibaba Cloud
security-group ports 6185 or 6199; access the dashboard with:

```bash
ssh -L 6185:127.0.0.1:6185 user@server
```

From the local checkout, set `RENNEBOT_DEPLOY_HOST=user@server` and run
`./deploy.ps1`. The script tests locally, pushes the current local `HEAD` to `master`,
backs up the server SQLite database, builds the current source checkout, and
restarts the container. It does not copy `.env` files or runtime data to the
server. A code rollback is
`./deploy.ps1 -Commit <full-40-character-SHA> -NoPush`; it retains the remote
`master` branch and deploys an earlier commit already present in its history.
