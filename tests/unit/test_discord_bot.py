"""Tests for Discord bot command handlers."""

from __future__ import annotations

import pytest

from bernstein.core.communication.discord_bot import (
    BotConfig,
    CommandResult,
    DiscordCommand,
    format_embed,
    get_available_commands,
    handle_demo,
    handle_help,
    handle_status,
    parse_command,
)

# ---------------------------------------------------------------------------
# DiscordCommand dataclass
# ---------------------------------------------------------------------------


class TestDiscordCommand:
    """DiscordCommand creation and immutability."""

    def test_create(self) -> None:
        cmd = DiscordCommand(name="ping", description="Pong!", handler_name="handle_ping")
        assert cmd.name == "ping"
        assert cmd.description == "Pong!"
        assert cmd.handler_name == "handle_ping"

    def test_frozen(self) -> None:
        cmd = DiscordCommand(name="a", description="b", handler_name="c")
        with pytest.raises(AttributeError):
            cmd.name = "x"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# BotConfig dataclass
# ---------------------------------------------------------------------------


class TestBotConfig:
    """BotConfig defaults and immutability."""

    def test_defaults(self) -> None:
        cfg = BotConfig()
        assert cfg.token_env_var == "BERNSTEIN_DISCORD_TOKEN"
        assert cfg.prefix == "/"
        assert cfg.guild_id is None

    def test_custom_values(self) -> None:
        cfg = BotConfig(token_env_var="MY_TOKEN", prefix="!", guild_id="12345")
        assert cfg.token_env_var == "MY_TOKEN"
        assert cfg.prefix == "!"
        assert cfg.guild_id == "12345"

    def test_frozen(self) -> None:
        cfg = BotConfig()
        with pytest.raises(AttributeError):
            cfg.prefix = "!"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# CommandResult dataclass
# ---------------------------------------------------------------------------


class TestCommandResult:
    """CommandResult creation and immutability."""

    def test_defaults(self) -> None:
        result = CommandResult(content="hello")
        assert result.content == "hello"
        assert result.embed_data is None
        assert result.ephemeral is False

    def test_with_embed(self) -> None:
        embed = {"title": "t"}
        result = CommandResult(content="x", embed_data=embed, ephemeral=True)
        assert result.embed_data == {"title": "t"}
        assert result.ephemeral is True

    def test_frozen(self) -> None:
        result = CommandResult(content="hi")
        with pytest.raises(AttributeError):
            result.content = "bye"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# handle_demo
# ---------------------------------------------------------------------------


class TestHandleDemo:
    """Demo command handler."""

    def test_returns_command_result(self) -> None:
        result = handle_demo("")
        assert isinstance(result, CommandResult)

    def test_content_contains_sample_run(self) -> None:
        result = handle_demo("")
        assert "Sample Orchestration Run" in result.content
        assert "bernstein run" in result.content

    def test_embed_present(self) -> None:
        result = handle_demo("")
        assert result.embed_data is not None
        assert result.embed_data["title"] == "Orchestration Demo"

    def test_embed_has_fields(self) -> None:
        result = handle_demo("")
        fields = result.embed_data["fields"]  # type: ignore[index]
        names = [f["name"] for f in fields]
        assert "Stages" in names
        assert "Tasks" in names
        assert "Agents" in names


# ---------------------------------------------------------------------------
# handle_help
# ---------------------------------------------------------------------------


class TestHandleHelp:
    """Help command handler."""

    def test_known_topic_setup(self) -> None:
        result = handle_help("setup")
        assert "Getting Started" in result.content
        assert result.ephemeral is True

    def test_known_topic_plans(self) -> None:
        result = handle_help("plans")
        assert "Plan Files" in result.content

    def test_known_topic_adapters(self) -> None:
        result = handle_help("adapters")
        assert "CLI Agent Adapters" in result.content

    def test_known_topic_quality_gates(self) -> None:
        result = handle_help("quality-gates")
        assert "Quality Gates" in result.content

    def test_case_insensitive(self) -> None:
        result = handle_help("SETUP")
        assert "Getting Started" in result.content

    def test_strips_whitespace(self) -> None:
        result = handle_help("  plans  ")
        assert "Plan Files" in result.content

    def test_unknown_topic_lists_available(self) -> None:
        result = handle_help("unknown")
        assert "Available help topics" in result.content
        assert "`setup`" in result.content
        assert "`plans`" in result.content

    def test_empty_topic_lists_available(self) -> None:
        result = handle_help("")
        assert "Available help topics" in result.content


# ---------------------------------------------------------------------------
# handle_status
# ---------------------------------------------------------------------------


class TestHandleStatus:
    """Status command handler."""

    def test_returns_command_result(self) -> None:
        result = handle_status()
        assert isinstance(result, CommandResult)

    def test_content_contains_version(self) -> None:
        result = handle_status()
        assert "v0.1.0" in result.content

    def test_content_contains_adapters(self) -> None:
        result = handle_status()
        assert "17 adapters" in result.content

    def test_embed_present(self) -> None:
        result = handle_status()
        assert result.embed_data is not None
        assert result.embed_data["title"] == "Bernstein Status"


# ---------------------------------------------------------------------------
# get_available_commands
# ---------------------------------------------------------------------------


class TestGetAvailableCommands:
    """Command listing."""

    def test_returns_list(self) -> None:
        cmds = get_available_commands()
        assert isinstance(cmds, list)
        assert len(cmds) >= 3

    def test_all_discord_commands(self) -> None:
        cmds = get_available_commands()
        for cmd in cmds:
            assert isinstance(cmd, DiscordCommand)

    def test_contains_demo(self) -> None:
        names = [c.name for c in get_available_commands()]
        assert "demo" in names

    def test_contains_help(self) -> None:
        names = [c.name for c in get_available_commands()]
        assert "help" in names

    def test_contains_status(self) -> None:
        names = [c.name for c in get_available_commands()]
        assert "status" in names


# ---------------------------------------------------------------------------
# format_embed
# ---------------------------------------------------------------------------


class TestFormatEmbed:
    """Embed dictionary builder."""

    def test_basic_structure(self) -> None:
        embed = format_embed(title="T", description="D", fields=[])
        assert embed["title"] == "T"
        assert embed["description"] == "D"
        assert embed["color"] == 0x5865F2
        assert embed["fields"] == []

    def test_custom_color(self) -> None:
        embed = format_embed(title="T", description="D", fields=[], color=0xFF0000)
        assert embed["color"] == 0xFF0000

    def test_fields_inline_default(self) -> None:
        embed = format_embed(
            title="T",
            description="D",
            fields=[{"name": "n", "value": "v"}],
        )
        assert embed["fields"][0]["inline"] is False

    def test_fields_inline_explicit(self) -> None:
        embed = format_embed(
            title="T",
            description="D",
            fields=[{"name": "n", "value": "v", "inline": True}],
        )
        assert embed["fields"][0]["inline"] is True


# ---------------------------------------------------------------------------
# parse_command
# ---------------------------------------------------------------------------


class TestParseCommand:
    """Command parser."""

    def test_simple_command(self) -> None:
        cmd, args = parse_command("/demo")
        assert cmd == "demo"
        assert args == ""

    def test_command_with_args(self) -> None:
        cmd, args = parse_command("/help setup")
        assert cmd == "help"
        assert args == "setup"

    def test_command_with_multi_args(self) -> None:
        cmd, args = parse_command("/help quality gates stuff")
        assert cmd == "help"
        assert args == "quality gates stuff"

    def test_no_prefix_returns_empty(self) -> None:
        cmd, args = parse_command("hello world")
        assert cmd == ""
        assert args == ""

    def test_custom_prefix(self) -> None:
        cmd, args = parse_command("!status", prefix="!")
        assert cmd == "status"
        assert args == ""

    def test_case_normalised(self) -> None:
        cmd, _ = parse_command("/DEMO")
        assert cmd == "demo"

    def test_prefix_only_returns_empty(self) -> None:
        cmd, args = parse_command("/")
        assert cmd == ""
        assert args == ""

    def test_empty_string_returns_empty(self) -> None:
        cmd, args = parse_command("")
        assert cmd == ""
        assert args == ""

    def test_multichar_prefix(self) -> None:
        cmd, args = parse_command("b!demo arg", prefix="b!")
        assert cmd == "demo"
        assert args == "arg"
