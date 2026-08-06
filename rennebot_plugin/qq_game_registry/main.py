"""AstrBot entrypoint for controlled QQ Official Bot message handling."""

from __future__ import annotations

import os

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.message.components import At, Plain
from astrbot.core.star.star_tools import StarTools

from .ai_client import AIConfigurationError, AIRequestError, OpenAICompatibleClient
from .commands import (
    CommandError,
    CommandKind,
    ParsedCommand,
    message_text_from_plain_components,
    parse_group_command,
    parse_runtime_config_command,
)
from .database import PluginDatabase


_PRIVATE_CONTEXT_MAX_CHARS_DEFAULT = 120_000
_PRIVATE_CONTEXT_RECENT_MESSAGES_DEFAULT = 24
_PRIVATE_SUMMARY_MAX_CHARS = 4_000


def _configured_ids(variable: str) -> set[str]:
    """Parse a comma-separated platform-ID environment variable.

    Args:
        variable: Environment variable name.

    Returns:
        Non-empty platform IDs from the setting.
    """
    return {item.strip() for item in os.getenv(variable, "").split(",") if item.strip()}


def _positive_int(variable: str, default: int) -> int:
    """Read a positive integer environment setting with a safe fallback.

    Args:
        variable: Environment variable name.
        default: Value used for missing or invalid input.

    Returns:
        A positive integer.
    """
    try:
        value = int(os.getenv(variable, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


class Main(Star):
    """Handle QQ Official messages before AstrBot's default LLM pipeline."""

    def __init__(self, context: Context) -> None:
        """Initialize persistent storage and the explicitly invoked AI client.

        Args:
            context: AstrBot plugin context.
        """
        super().__init__(context)
        self.database = PluginDatabase(StarTools.get_data_dir("qq_game_registry") / "rennebot.sqlite3")
        self.database.initialize()
        if self.database.get_setting("admin_user_ids") is None:
            bootstrap_admins = _configured_ids("RENNEBOT_BOOTSTRAP_ADMIN_IDS")
            if bootstrap_admins:
                self.database.set_setting("admin_user_ids", sorted(bootstrap_admins))
        self.ai_client = OpenAICompatibleClient()
        self.private_context_max_chars = _positive_int(
            "AI_PRIVATE_CONTEXT_MAX_CHARS", _PRIVATE_CONTEXT_MAX_CHARS_DEFAULT
        )
        self.private_context_recent_messages = _positive_int(
            "AI_PRIVATE_CONTEXT_RECENT_MESSAGES",
            _PRIVATE_CONTEXT_RECENT_MESSAGES_DEFAULT,
        )

    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    @filter.platform_adapter_type(filter.PlatformAdapterType.QQOFFICIAL)
    async def route_message(self, event: AstrMessageEvent):
        """Route permitted messages and block the normal AstrBot LLM flow.

        Args:
            event: Incoming QQ Official message event.

        Yields:
            A plain response when a command or an authorized AI request needs one.
        """
        try:
            group_id = event.get_group_id()
            sender_id = event.get_sender_id()
            messages = event.get_messages()
            message = message_text_from_plain_components(
                (
                    component.text
                    for component in messages
                    if isinstance(component, Plain)
                ),
                event.message_str,
            )
            if group_id:
                mentioned_bot = any(
                    isinstance(component, At) for component in messages
                )
                response = (
                    await self._handle_group_message(event, group_id, sender_id, message)
                    if mentioned_bot
                    else None
                )
            elif message == "/renne-id":
                response = f"你的 UserID 是：{sender_id}"
            elif message.startswith("/renne-config"):
                response = self._handle_config_message(sender_id, message)
            elif sender_id in self._setting_ids("ai_private_user_ids") and message:
                response = await self._handle_private_ai_message(sender_id, message)
            else:
                response = None
            if response:
                yield event.plain_result(response)
        except Exception as error:
            self.logger.exception("qq_game_registry message handling failed: %s", error)
            yield event.plain_result("处理消息时发生了错误，请稍后再试。")
        finally:
            event.stop_event()

    async def _handle_group_message(
        self,
        event: AstrMessageEvent,
        group_id: str,
        sender_id: str,
        message: str,
    ) -> str | None:
        """Handle a command in one group.

        Args:
            event: Incoming QQ Official message event.
            group_id: QQ group platform ID.
            sender_id: QQ sender platform ID.
            message: Plain message text.

        Returns:
            Reply text, or None when a normal group message should be ignored.
        """
        try:
            command = parse_group_command(message)
        except CommandError as error:
            return str(error)
        if command is None:
            return None
        if command.kind == CommandKind.AI:
            if group_id not in self._setting_ids("ai_group_ids"):
                return "此群未启用 AI。"
            return await self._ask_ai(command.prompt or "")
        if command.kind == CommandKind.HELP:
            return self._help_text()
        if command.kind == CommandKind.IDENTITY:
            return f"群 ID 是：{group_id}\n你的 UserID 是：{sender_id}"
        return self._handle_registry_command(event, group_id, sender_id, command)

    def _handle_registry_command(
        self,
        event: AstrMessageEvent,
        group_id: str,
        sender_id: str,
        command: ParsedCommand,
    ) -> str:
        """Perform a validated registry command.

        Args:
            event: Incoming QQ Official message event.
            group_id: QQ group platform ID.
            sender_id: QQ sender platform ID.
            command: Parsed registry command.

        Returns:
            User-facing operation result.
        """
        if command.kind == CommandKind.RECORD_GAME_ID:
            display_name = event.get_sender_name() or sender_id
            self.database.upsert_game_id(
                group_id,
                sender_id,
                display_name,
                command.game_name or "",
                command.game_id or "",
            )
            return f"已记录 {display_name} 的《{command.game_name}》ID：{command.game_id}。"
        if command.kind == CommandKind.QUERY_GAME_ID:
            records = self.database.list_game_ids(group_id, command.game_name or "")
            if not records:
                return f"本群还没有《{command.game_name}》的登记记录。"
            lines = [f"《{command.game_name}》群友 ID："]
            lines.extend(f"{record.display_name}：{record.game_id}" for record in records)
            return "\n".join(lines)
        if command.kind == CommandKind.DELETE_GAME_ID:
            target_user_id = command.target_user_id or sender_id
            if target_user_id != sender_id and sender_id not in self._setting_ids(
                "admin_user_ids"
            ):
                return "只能删除自己的记录；机器人管理员可以指定 QQ 号删除。"
            deleted = self.database.delete_game_id(
                group_id,
                target_user_id,
                command.game_name or "",
            )
            return "已删除记录。" if deleted else "没有找到可删除的记录。"
        return "未知指令。"

    def _handle_config_message(self, sender_id: str, message: str) -> str:
        """Update or display private runtime configuration for an administrator.

        Args:
            sender_id: QQ sender platform ID.
            message: Private command text.

        Returns:
            User-facing configuration result.
        """
        if sender_id not in self._setting_ids("admin_user_ids"):
            return "你还不是 RenneBot 管理员。"
        try:
            command = parse_runtime_config_command(message)
        except CommandError as error:
            return str(error)
        if command is None:
            return "未知配置指令。"
        if command.action == "show":
            private_users = ", ".join(sorted(self._setting_ids("ai_private_user_ids")))
            groups = ", ".join(sorted(self._setting_ids("ai_group_ids")))
            admins = ", ".join(sorted(self._setting_ids("admin_user_ids")))
            return (
                f"AI 私聊白名单：{private_users or '（未配置）'}\n"
                f"AI 群聊白名单：{groups or '（未配置）'}\n"
                f"机器人管理员：{admins or '（未配置）'}"
            )
        if command.setting_key == "admin_user_ids" and sender_id not in command.values:
            return "管理员列表必须保留你自己的 ID，避免失去管理权限。"
        self.database.set_setting(command.setting_key or "", sorted(set(command.values)))
        setting_names = {
            "ai_private_user_ids": "AI 私聊白名单",
            "ai_group_ids": "AI 群聊白名单",
            "admin_user_ids": "机器人管理员",
        }
        setting_name = setting_names.get(command.setting_key or "", "配置")
        return f"已更新{setting_name}，共 {len(command.values)} 个 ID。"

    async def _handle_private_ai_message(self, sender_id: str, message: str) -> str | None:
        """Handle an authorized user's persistent private AI conversation.

        Args:
            sender_id: QQ platform user ID that owns the conversation.
            message: Plain text message sent in the private chat.

        Returns:
            A response when a command or active conversation handles the message.
        """
        conversation = self.database.get_private_ai_conversation(sender_id)
        if message == "开启新对话":
            self.database.set_private_ai_conversation(sender_id, True, "", [])
            return "已开启新对话。之后的普通消息会保留上下文；发送“清理上下文”可重置记忆。"
        if message == "清理上下文":
            if not conversation.active:
                return "当前没有开启中的 AI 对话。发送“开启新对话”开始。"
            self.database.set_private_ai_conversation(sender_id, True, "", [])
            return "上下文已清理，当前对话保持开启。"
        if message == "结束对话":
            self.database.set_private_ai_conversation(
                sender_id,
                False,
                conversation.summary,
                conversation.messages,
            )
            return "AI 对话已结束。发送“开启新对话”可重新开始。"
        if not conversation.active:
            return None
        return await self._ask_private_ai(
            sender_id,
            conversation.summary,
            conversation.messages,
            message,
        )

    async def _ask_private_ai(
        self,
        sender_id: str,
        summary: str,
        messages: list[dict[str, str]],
        prompt: str,
    ) -> str:
        """Reply with persisted context and summarize older turns when needed.

        Args:
            sender_id: QQ platform user ID that owns the conversation.
            summary: Compact memory of previous conversation turns.
            messages: Recent user and assistant messages.
            prompt: Current user message.

        Returns:
            The AI response sent to the private chat.
        """
        recent_messages = [*messages, {"role": "user", "content": prompt}]
        context_chars = len(summary) + sum(
            len(message["content"]) for message in recent_messages
        )
        if context_chars > self.private_context_max_chars and len(recent_messages) > 1:
            keep_count = min(self.private_context_recent_messages, len(recent_messages) - 1)
            archived_messages = recent_messages[:-keep_count]
            recent_messages = recent_messages[-keep_count:]
            summary = await self._summarize_private_context(summary, archived_messages)

        request_messages: list[dict[str, str]] = []
        if summary:
            request_messages.append(
                {
                    "role": "system",
                    "content": f"Conversation memory:\n{summary}",
                }
            )
        request_messages.extend(recent_messages)
        response = await self.ai_client.ask_messages(request_messages)
        self.database.set_private_ai_conversation(
            sender_id,
            True,
            summary,
            [*recent_messages, {"role": "assistant", "content": response}],
        )
        return response

    async def _summarize_private_context(
        self, summary: str, messages: list[dict[str, str]]
    ) -> str:
        """Compress older private conversation turns into durable memory.

        Args:
            summary: Existing compact memory, if any.
            messages: Older messages that no longer fit in the recent window.

        Returns:
            A bounded summary that retains facts, preferences, and open tasks.
        """
        transcript = "\n".join(
            f"{message['role']}: {message['content']}" for message in messages
        )
        prompt = (
            "Summarize this conversation for future continuation. Preserve stable facts, "
            "user preferences, decisions, numbers, constraints, and unresolved tasks. "
            "Do not include hidden reasoning. Keep the summary under 4000 characters.\n\n"
            f"Existing memory:\n{summary or '(none)'}\n\n"
            f"Older conversation:\n{transcript}"
        )
        return (await self.ai_client.ask(prompt))[:_PRIVATE_SUMMARY_MAX_CHARS]

    def _setting_ids(self, key: str) -> set[str]:
        """Read a platform-ID setting stored in SQLite.

        Args:
            key: Plugin setting key.

        Returns:
            String platform IDs stored under the key.
        """
        value = self.database.get_setting(key, [])
        if not isinstance(value, list):
            return set()
        return {item for item in value if isinstance(item, str)}

    async def _ask_ai(self, prompt: str) -> str:
        """Return a safe user-facing result from the configured AI endpoint.

        Args:
            prompt: Authorized user prompt.

        Returns:
            AI response or a safe error message.
        """
        try:
            return await self.ai_client.ask(prompt)
        except (AIConfigurationError, AIRequestError) as error:
            return str(error)

    @staticmethod
    def _help_text() -> str:
        """Return the group command reference.

        Returns:
            Human-readable command list.
        """
        return (
            "可用指令：\n"
            "/记录游戏id <游戏名> <数字ID>\n"
            "/查询群友id <游戏名>\n"
            "/删除游戏id <游戏名> [QQ号]\n"
            "/ai <问题>（仅已授权群）\n"
            "/renne-id"
        )
