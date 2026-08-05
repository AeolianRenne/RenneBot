import pytest

from rennebot_plugin.qq_game_registry.commands import (
    CommandError,
    CommandKind,
    parse_group_command,
)


def test_parses_game_id_record() -> None:
    command = parse_group_command("/记录游戏id 原神 123456")

    assert command is not None
    assert command.kind == CommandKind.RECORD_GAME_ID
    assert command.game_name == "原神"
    assert command.game_id == "123456"


@pytest.mark.parametrize(
    "message",
    [
        "/记录游戏id 原神 abc",
        "/记录游戏id 原神",
        "/记录游戏id 原神 1 2",
    ],
)
def test_rejects_invalid_record_syntax(message: str) -> None:
    with pytest.raises(CommandError):
        parse_group_command(message)


def test_parses_query_and_delete() -> None:
    query = parse_group_command("/查询群友id 原神")
    delete = parse_group_command("/删除游戏id 原神 10001")

    assert query is not None
    assert query.kind == CommandKind.QUERY_GAME_ID
    assert delete is not None
    assert delete.target_user_id == "10001"


def test_parses_identity_command() -> None:
    command = parse_group_command("/renne-id")

    assert command is not None
    assert command.kind == CommandKind.IDENTITY


def test_non_command_is_ignored() -> None:
    assert parse_group_command("大家晚上好") is None
