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

- A private AI conversation is available only when its sender ID is stored in
  the SQLite runtime configuration. The user sends `开启新对话` to enable it;
  ordinary private messages then retain context. `清理上下文` clears memory
  while keeping the conversation active, and `结束对话` returns the chat to
  its silent state.
- A group request must be `@机器人 /ai <问题>` and its group ID must be stored
  in the SQLite runtime configuration.
- All groups where the bot is present can use the non-AI commands below:
  - `@机器人 /记录游戏id <游戏名> <数字ID>`
  - `@机器人 /查询群友id <游戏名>`
  - `@机器人 /删除游戏id <游戏名> [QQ号]`
- A member may delete only their own record. IDs in `BOT_ADMIN_USER_IDS` may
  delete another member's record by supplying that member's QQ ID.

Use `/renne-id` to obtain platform IDs: send it in a private chat to see your
user ID, or send `@机器人 /renne-id` in a group to see the group and sender IDs.
On the first startup only, set `RENNEBOT_BOOTSTRAP_ADMIN_IDS` as a one-time
runtime environment variable. It initializes the SQLite administrator list;
then recreate the service without that variable. An administrator privately
manages all allowlists with:

```text
/renne-config show
/renne-config ai-users set <id,id>
/renne-config ai-groups set <id,id>
/renne-config admins set <id,id>
```

Private conversation state is stored in SQLite cache namespace `private_ai`.
When its character budget is exceeded, older messages are summarized and the
most recent messages are retained. Configure the defaults outside Git with
`AI_PRIVATE_CONTEXT_MAX_CHARS=120000` and
`AI_PRIVATE_CONTEXT_RECENT_MESSAGES=24`. A private message longer than
`AI_PRIVATE_MESSAGE_MAX_CHARS=8000` is rejected before it is sent to the model
or written to SQLite. Group `/ai` requests remain one-shot.

### Private AI safety boundary

Private AI requests use a fixed, non-overridable system safety prompt. The model
is not given any tools or access to the server, Docker containers, filesystem,
SQLite database, logs, runtime configuration, Git checkout, other user data, or
AstrBot credentials. It must not claim to access, infer, or disclose them, and
it must refuse requests for secrets, internal instructions, dangerous server
operations, or any information about the developer. Developer-related questions
are blocked before they reach the model and are not stored in conversation
context.

Credential-like inputs (API keys, tokens, passwords, private keys, and common
cloud access-key formats) are rejected before a private AI request is made and
are not stored in the conversation. Existing persisted conversation summaries
and messages are redacted before use, and AI responses are redacted before they
are stored or returned. This is defense in depth, not a substitute for rotating
a secret that was already exposed elsewhere.

## External runtime configuration recovery

The plugin settings are ordinary JSON values in SQLite table `plugin_settings`.
They can be recovered or changed without a QQ administrator account. Stop the
container first, back up the database, then use the repository's local tool:

```bash
cd /opt/rennebot/app
database=/opt/rennebot/runtime/astrbot-data/plugin_data/qq_game_registry/rennebot.sqlite3
cp -a "$database" "/opt/rennebot/runtime/backups/rennebot-manual-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"

BOT_ENV_FILE=/opt/rennebot/runtime/bot.env RUNTIME_DIR=/opt/rennebot/runtime \
  docker compose -f compose.rennebot.yml stop
python3 scripts/rennebot-db.py --database "$database" set admins <new-admin-id>
python3 scripts/rennebot-db.py --database "$database" set ai-users <id-1> <id-2>
python3 scripts/rennebot-db.py --database "$database" set ai-groups <group-id>
python3 scripts/rennebot-db.py --database "$database" show
BOT_ENV_FILE=/opt/rennebot/runtime/bot.env RUNTIME_DIR=/opt/rennebot/runtime \
  docker compose -f compose.rennebot.yml start
```

`set-json <key> <json>` can write any future plugin setting directly. This tool
has full access to the runtime configuration; restrict filesystem and SSH access
to trusted server administrators.

## Alibaba Cloud server

Run `scripts/server-bootstrap-rennebot.sh <repository-url>` once on the Linux
server after Docker is installed and a read-only Git deploy key is configured.
Then edit `/opt/rennebot/runtime/bot.env` with the OpenAI-compatible API and
start the Compose service as shown by the script. Do not place allowlists in
this file.

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
