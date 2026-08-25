# RenneBot deployment

This repository contains an AstrBot source checkout and pins the self-contained
RenneBot plugin project as the `rennebot_plugin` Git submodule. The plugin source
is mounted read-only into AstrBot at runtime. Its SQLite database lives in
AstrBot's persistent plugin data directory and is not part of Git.

`rennebot_plugin/qq_game_registry/main.py` is deliberately only the AstrBot event
adapter. Command syntax remains in `commands.py`; feature behavior lives in
`scripts/group_registry.py`, `scripts/private_ai.py`, `scripts/runtime_config.py`,
and `scripts/safety.py`. This submodule is the RenneBot behavior boundary; do not
add RenneBot behavior to AstrBot core.

## Upstream strategy

Keep this source fork only as a transition path. The supported long-term layout is
one small deployment repository that pins an AstrBot image version or digest and
includes `rennebot_plugin` as a Git submodule. The runtime volume remains the
same, so SQLite and AstrBot WebUI configuration survive that future migration.

While this deployment repository still tracks AstrBot source, configure the
original AstrBot repository as an `upstream` remote and merge or rebase deliberately
tested releases. Do not deploy an unreviewed `latest` image. The plugin metadata
currently declares compatibility with AstrBot 4.x; test any major-version update
before production deployment.

Clone the deployment repository with `git clone --recurse-submodules`. When
updating a checkout, run `git pull --ff-only`, `git submodule sync --recursive`,
then `git submodule update --init --recursive`. Keep the mount path unchanged so
the existing `/AstrBot/data/plugins/qq_game_registry` data directory is retained.

For a plugin-only feature release, commit and push inside `rennebot_plugin`, then
return to the deployment repository, stage the updated submodule pointer, commit,
and push the deployment repository. The plugin is public but its submodule URL is
still SSH, so verify `git ls-remote git@github.com:AeolianRenne/astrbot-plugin-rennebot.git`
from the server before the first update. If its repository-scoped deploy key cannot
read the submodule, use a single normal SSH key from a dedicated GitHub machine
account with read access to the deployment repository; do not use a personal
account key on the server.

## Local development

```powershell
cd C:\Users\ASUS\Documents\QQAgent\RenneBot\RenneBot
uv sync
uv run pytest --confcutdir=rennebot_plugin/tests rennebot_plugin/tests
uv run ruff check rennebot_plugin
Copy-Item .env.rennebot.example .env.rennebot
docker compose -f compose.rennebot.yml up -d --build
```

The dashboard is bound to `127.0.0.1:6185`. After the container starts, sign in,
change the generated AstrBot password, and create a **QQ Official Bot
(WebSocket)** adapter. Enter the QQ AppID and AppSecret in the dashboard; AstrBot
persists that configuration in `runtime/astrbot-data`. Keep AstrBot's default LLM
chat disabled because RenneBot handles the allowed AI calls itself.

## Optional QQ personal-account adapter

RenneBot supports its existing **QQ Official Bot (WebSocket)** adapter and an
additional **OneBot v11** adapter at the same time. The latter is intended for a
dedicated QQ personal account connected through a compatible protocol client such
as NapCat. It is not a QQ Official Bot API; expect possible platform risk, account
login challenges, and compatibility changes. Do not use a primary personal QQ
account or expose a protocol client's management UI publicly.

Create the OneBot adapter in AstrBot WebUI under **机器人** → **创建机器人** →
**OneBot v11**. Enable it, set the reverse WebSocket host to `0.0.0.0`, keep port
`6199`, and set a long random reverse-WebSocket token. Configure the same token in
the protocol client. This Compose deployment publishes port 6199 only on host
loopback, so a protocol client installed directly on the server uses:

```text
ws://127.0.0.1:6199/ws
```

When the protocol client runs in Docker instead, attach it to AstrBot's Compose
network and use the AstrBot service hostname:

```text
ws://astrbot:6199/ws
```

Never add a security-group rule for port 6199. Verify the connection in AstrBot
WebUI **平台日志**: `aiocqhttp(OneBot v11) 适配器已连接。` indicates success.
The plugin ignores OneBot events sent by the connected QQ account itself, avoiding
self-reply loops.

## QQ behavior

The plugin intercepts QQ Official Bot and OneBot v11 messages before AstrBot's
normal LLM flow. Command behavior is the same across both adapters.

- **Every group response is disabled by default.** A group must be listed in
  `enabled_group_ids` before the bot responds to any mention, including game-ID,
  BP, `/renne-id`, help, or `/ai`. This prevents a connected personal QQ account
  from responding in unrelated existing groups. The AI group allowlist below is a
  separate second gate.
- A private AI conversation is available only when its sender ID is stored in
  the SQLite runtime configuration. The user sends `开启新对话` to enable it;
  ordinary private messages then retain context. `清理上下文` clears memory
  while keeping the conversation active, and `结束对话` returns the chat to
  its silent state.
- An authorized private user can instead send `开始任务：<目标>` (or the compatible
  alias `开启任务：<目标>`) from the inactive state. The bot immediately confirms
  that public-source retrieval has started, then sends the final result separately.
  A research task is mutually exclusive with an ordinary
  conversation: end a conversation before starting a task, and send
  `结束当前任务` (or the compatible `结束任务`) before starting a new conversation.
  The bot confirms in Chinese when the task has ended. During the task, ordinary
  messages use bounded public-web search and return source links. Group behavior
  remains unchanged.
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
The OneBot personal-account adapter normally returns numeric QQ IDs; the QQ Official
Bot adapter can return a different platform ID format. Add the correct ID for each
adapter to the same SQLite settings. For example, retain your existing administrator
ID and add the personal-account QQ ID with `/renne-config admins set
<official-id>,<personal-qq-id>`; use the analogous `ai-users` and `ai-groups`
commands for private-AI users and group AI authorization.
On the first startup only, set `RENNEBOT_BOOTSTRAP_ADMIN_IDS` as a one-time
runtime environment variable. It initializes the SQLite administrator list;
then recreate the service without that variable. An administrator privately
manages all allowlists with:

```text
/renne-config show
/renne-config groups set <group-id,group-id>
/renne-config ai-users set <id,id>
/renne-config ai-groups set <id,id>
/renne-config admins set <id,id>
```

Before deploying this change, record the ID of every currently authorized QQ
Official Bot group with `@机器人 /renne-id`, then add those IDs using
`/renne-config groups set ...`. For the OneBot personal account, the group ID is
normally the numeric QQ group number. The `groups set` command replaces the whole
list, so always include every group that should remain enabled.

Private conversation state is stored in SQLite cache namespace `private_ai`.
When its character budget is exceeded, older messages are summarized and the
most recent messages are retained. Configure the defaults outside Git with
`AI_PRIVATE_CONTEXT_MAX_CHARS=120000` and
`AI_PRIVATE_CONTEXT_RECENT_MESSAGES=24`. A private message longer than
`AI_PRIVATE_MESSAGE_MAX_CHARS=8000` is rejected before it is sent to the model
or written to SQLite. Group `/ai` requests remain one-shot.

Research tasks use Tavily through `TAVILY_API_KEY` in the server-only `bot.env`.
For every filtered result, RenneBot also attempts to extract visible text from the
corresponding **public HTML** page; successful citations identify it as `公开网页正文`.
If a page is unavailable, has a login or payment wall, is non-HTML, or exceeds a
limit, the task retains Tavily's `搜索摘要` instead. It never stores account
credentials or cookies, sends authorization headers, uses an environment proxy,
or bypasses access controls.

Public-page extraction permits only HTTP(S) on ports 80/443, rejects credentialed
URLs, local hostnames and direct private/reserved IPs, validates every redirect,
resolves each destination before connecting, and limits redirects, total request
time, decompressed response bytes, and extracted text. This prevents the feature
from being used to query local Docker services, the cloud metadata service, or
arbitrary files. Tune `AI_RESEARCH_TIMEOUT_SECONDS=20`,
`AI_RESEARCH_MAX_SOURCES=6`, `AI_RESEARCH_MAX_QUERIES=6`, the total logical
operation limit `AI_RESEARCH_MAX_REQUESTS=12`, `AI_RESEARCH_MAX_ROUNDS=2`, and the
user-scoped
`AI_RESEARCH_CACHE_TTL_SECONDS=900`, `AI_RESEARCH_TASK_TIMEOUT_SECONDS=90`,
`AI_RESEARCH_EXTRACT_TIMEOUT_SECONDS=12`, `AI_RESEARCH_EXTRACT_MAX_BYTES=1048576`,
and `AI_RESEARCH_EXTRACT_MAX_CHARS=6000` there. Never place search keys in Git or
SQLite.

Research is staged rather than tied to any one task type. The first round uses the
task objective (and official-domain queries for named providers such as DeepSeek,
Qwen/通义千问, Kimi/Moonshot, MiniMax, or GLM/智谱). When capacity remains, a second
round asks the model to return JSON-only plain-text queries that fill gaps revealed
by the first-round evidence. The model cannot request URLs or make network calls;
every follow-up still goes only to Tavily and every returned URL must pass the same
public-web safety checks. Searches and page extraction run in parallel once their
stage is planned, then sources are deduplicated and capped by
`AI_RESEARCH_MAX_SOURCES`. Each Tavily query and each public-page extraction consumes
one operation from `AI_RESEARCH_MAX_REQUESTS`; cached results do not initiate a
network request. Set `AI_RESEARCH_MAX_ROUNDS=1` to disable the follow-up round, or
use `AI_RESEARCH_MAX_ROUNDS=2` for broad, multi-topic research; higher values are
currently capped at two rounds. Use
`AI_RESEARCH_MAX_SOURCES=6`, `AI_RESEARCH_MAX_QUERIES=6`, and
`AI_RESEARCH_MAX_REQUESTS=12`. The recommended
`AI_RESEARCH_TASK_TIMEOUT_SECONDS=90` is a hard budget for each task turn, covering
planning, search, extraction, and the final model call; a timeout returns a Chinese
status message and does not ask the model to infer a conclusion from incomplete
evidence.

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
cd /home/admin/RenneBot
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
backs up the server SQLite database, and recreates the container. Plugin source is
mounted from the checked-out revision, so a plugin-only release does not rebuild
the AstrBot image. It does not copy `.env` files or runtime data to the
server. A code rollback is
`./deploy.ps1 -Commit <full-40-character-SHA> -NoPush`; it retains the remote
`master` branch and deploys an earlier commit already present in its history.
