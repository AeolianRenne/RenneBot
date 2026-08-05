"""AstrBot entrypoint for controlled QQ Official Bot message handling."""

from __future__ import annotations

import os

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.message.components import At
from astrbot.core.star.star_tools import StarTools

from .ai_client import AIConfigurationError, AIRequestError, OpenAICompatibleClient
from .commands import (
    CommandError,
    CommandKind,
    ParsedCommand,
    parse_group_command,
    parse_runtime_config_command,
)
from .database import PluginDatabase


def _configured_ids(variable: str) -> set[str]:
    """Parse a comma-separated platform-ID environment variable.

    Args:
        variable: Environment variable name.

    Returns:
        Non-empty platform IDs from the setting.
    """
    return {item.strip() for item in os.getenv(variable, "").split(",") if item.strip()}


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
            message = event.message_str.strip()
            if group_id:
                messages = event.get_messages()
                mentioned_bot = bool(
                    messages
                    and isinstance(messages[0], At)
                    and str(messages[0].qq) == str(event.get_self_id())
                )
                response = (
                    await self._handle_group_message(event, group_id, sender_id, message)
                    if mentioned_bot
                    else None
                )
            elif message == "/renne-id":
                response = f"Your platform user ID: {sender_id}"
            elif message.startswith("/renne-config"):
                response = self._handle_config_message(sender_id, message)
            elif sender_id in self._setting_ids("ai_private_user_ids") and message:
                response = await self._ask_ai(message)
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
            return f"Group ID: {group_id}\nYour platform user ID: {sender_id}"
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
            return "You are not a RenneBot administrator."
        try:
            command = parse_runtime_config_command(message)
        except CommandError as error:
            return str(error)
        if command is None:
            return "Unknown configuration command."
        if command.action == "show":
            private_users = ", ".join(sorted(self._setting_ids("ai_private_user_ids")))
            groups = ", ".join(sorted(self._setting_ids("ai_group_ids")))
            admins = ", ".join(sorted(self._setting_ids("admin_user_ids")))
            return (
                f"AI private users: {private_users or '(none)'}\n"
                f"AI groups: {groups or '(none)'}\n"
                f"Administrators: {admins or '(none)'}"
            )
        if command.setting_key == "admin_user_ids" and sender_id not in command.values:
            return "Keep your own ID in the administrator list to avoid losing access."
        self.database.set_setting(command.setting_key or "", sorted(set(command.values)))
        return f"Updated {command.setting_key} with {len(command.values)} ID(s)."

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
