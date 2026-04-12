"""Tests for composable sandbox profiles."""

from __future__ import annotations

import pytest

from bernstein.core.security.sandbox_profiles import (
    FileSystemRule,
    NetworkRule,
    ProfileConflict,
    SandboxProfile,
    compose_profiles,
    get_builtin_profile,
    list_builtin_profiles,
    render_profile_summary,
    validate_profile,
)

# ---------------------------------------------------------------------------
# NetworkRule
# ---------------------------------------------------------------------------


class TestNetworkRule:
    def test_basic_creation(self) -> None:
        rule = NetworkRule(host="localhost", port=5432, protocol="tcp")
        assert rule.host == "localhost"
        assert rule.port == 5432
        assert rule.protocol == "tcp"

    def test_default_protocol(self) -> None:
        rule = NetworkRule(host="10.0.0.1", port=80)
        assert rule.protocol == "tcp"

    def test_frozen(self) -> None:
        rule = NetworkRule(host="localhost", port=80)
        with pytest.raises(AttributeError):
            rule.port = 443  # type: ignore[misc]

    def test_empty_host_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            NetworkRule(host="", port=80)

    def test_negative_port_rejected(self) -> None:
        with pytest.raises(ValueError, match="0-65535"):
            NetworkRule(host="localhost", port=-1)

    def test_port_above_max_rejected(self) -> None:
        with pytest.raises(ValueError, match="0-65535"):
            NetworkRule(host="localhost", port=70000)

    def test_port_zero_allowed(self) -> None:
        """Port 0 means all ports -- valid but flagged by validate_profile."""
        rule = NetworkRule(host="0.0.0.0", port=0)
        assert rule.port == 0

    def test_equality(self) -> None:
        a = NetworkRule(host="localhost", port=5432, protocol="tcp")
        b = NetworkRule(host="localhost", port=5432, protocol="tcp")
        assert a == b

    def test_udp_protocol(self) -> None:
        rule = NetworkRule(host="localhost", port=53, protocol="udp")
        assert rule.protocol == "udp"


# ---------------------------------------------------------------------------
# FileSystemRule
# ---------------------------------------------------------------------------


class TestFileSystemRule:
    def test_basic_creation(self) -> None:
        rule = FileSystemRule(path="/app", permissions="read")
        assert rule.path == "/app"
        assert rule.permissions == "read"

    def test_default_permissions(self) -> None:
        rule = FileSystemRule(path="/data")
        assert rule.permissions == "read"

    def test_frozen(self) -> None:
        rule = FileSystemRule(path="/app")
        with pytest.raises(AttributeError):
            rule.permissions = "write"  # type: ignore[misc]

    def test_empty_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            FileSystemRule(path="")

    def test_write_permission(self) -> None:
        rule = FileSystemRule(path="/tmp", permissions="write")
        assert rule.permissions == "write"

    def test_execute_permission(self) -> None:
        rule = FileSystemRule(path="/usr/bin", permissions="execute")
        assert rule.permissions == "execute"


# ---------------------------------------------------------------------------
# SandboxProfile
# ---------------------------------------------------------------------------


class TestSandboxProfile:
    def test_minimal_profile(self) -> None:
        p = SandboxProfile(name="test")
        assert p.name == "test"
        assert p.network_rules == ()
        assert p.fs_rules == ()
        assert p.env_vars == ()
        assert p.allowed_commands == ()
        assert p.description == ""

    def test_frozen(self) -> None:
        p = SandboxProfile(name="test")
        with pytest.raises(AttributeError):
            p.name = "other"  # type: ignore[misc]

    def test_full_profile(self) -> None:
        net = (NetworkRule(host="localhost", port=80),)
        fs = (FileSystemRule(path="/app", permissions="write"),)
        p = SandboxProfile(
            name="full",
            network_rules=net,
            fs_rules=fs,
            env_vars=("HOME",),
            allowed_commands=("ls",),
            description="A full profile.",
        )
        assert len(p.network_rules) == 1
        assert len(p.fs_rules) == 1
        assert p.env_vars == ("HOME",)
        assert p.allowed_commands == ("ls",)


# ---------------------------------------------------------------------------
# compose_profiles
# ---------------------------------------------------------------------------


class TestComposeProfiles:
    def test_single_profile_returns_itself(self) -> None:
        p = SandboxProfile(name="solo")
        result = compose_profiles(p)
        assert result is p

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            compose_profiles()

    def test_two_profiles_union_network(self) -> None:
        a = SandboxProfile(
            name="a",
            network_rules=(NetworkRule(host="localhost", port=5432),),
        )
        b = SandboxProfile(
            name="b",
            network_rules=(NetworkRule(host="localhost", port=6379),),
        )
        result = compose_profiles(a, b)
        assert len(result.network_rules) == 2
        assert result.name == "a + b"

    def test_deduplication(self) -> None:
        rule = NetworkRule(host="localhost", port=80)
        a = SandboxProfile(name="a", network_rules=(rule,))
        b = SandboxProfile(name="b", network_rules=(rule,))
        result = compose_profiles(a, b)
        assert len(result.network_rules) == 1

    def test_env_vars_merged(self) -> None:
        a = SandboxProfile(name="a", env_vars=("FOO", "BAR"))
        b = SandboxProfile(name="b", env_vars=("BAR", "BAZ"))
        result = compose_profiles(a, b)
        assert set(result.env_vars) == {"FOO", "BAR", "BAZ"}

    def test_commands_merged(self) -> None:
        a = SandboxProfile(name="a", allowed_commands=("git", "make"))
        b = SandboxProfile(name="b", allowed_commands=("make", "docker"))
        result = compose_profiles(a, b)
        assert set(result.allowed_commands) == {"git", "make", "docker"}

    def test_fs_rules_merged(self) -> None:
        a = SandboxProfile(
            name="a",
            fs_rules=(FileSystemRule(path="/app", permissions="read"),),
        )
        b = SandboxProfile(
            name="b",
            fs_rules=(FileSystemRule(path="/tmp", permissions="write"),),
        )
        result = compose_profiles(a, b)
        assert len(result.fs_rules) == 2

    def test_description_merged(self) -> None:
        a = SandboxProfile(name="a", description="Backend")
        b = SandboxProfile(name="b", description="Frontend")
        result = compose_profiles(a, b)
        assert "Backend" in result.description
        assert "Frontend" in result.description

    def test_three_profiles(self) -> None:
        a = SandboxProfile(name="a", env_vars=("A",))
        b = SandboxProfile(name="b", env_vars=("B",))
        c = SandboxProfile(name="c", env_vars=("C",))
        result = compose_profiles(a, b, c)
        assert set(result.env_vars) == {"A", "B", "C"}
        assert result.name == "a + b + c"


# ---------------------------------------------------------------------------
# get_builtin_profile / list_builtin_profiles
# ---------------------------------------------------------------------------


class TestBuiltinProfiles:
    def test_web_backend(self) -> None:
        p = get_builtin_profile("web-backend")
        assert p.name == "web-backend"
        hosts_ports = {(r.host, r.port) for r in p.network_rules}
        assert ("localhost", 5432) in hosts_ports
        assert ("localhost", 6379) in hosts_ports

    def test_frontend_no_network(self) -> None:
        p = get_builtin_profile("frontend")
        assert p.network_rules == ()
        assert "npm" in p.allowed_commands

    def test_ci_runner_full_network(self) -> None:
        p = get_builtin_profile("ci-runner")
        assert len(p.network_rules) > 0
        assert any(r.port == 0 for r in p.network_rules)

    def test_minimal(self) -> None:
        p = get_builtin_profile("minimal")
        assert p.network_rules == ()
        assert p.allowed_commands == ()

    def test_unknown_raises(self) -> None:
        with pytest.raises(KeyError, match="Unknown builtin"):
            get_builtin_profile("nonexistent")

    def test_list_builtin_profiles(self) -> None:
        names = list_builtin_profiles()
        assert "web-backend" in names
        assert "frontend" in names
        assert "ci-runner" in names
        assert "minimal" in names
        # Sorted
        assert names == tuple(sorted(names))

    def test_all_builtins_are_frozen(self) -> None:
        for name in list_builtin_profiles():
            p = get_builtin_profile(name)
            with pytest.raises(AttributeError):
                p.name = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# validate_profile
# ---------------------------------------------------------------------------


class TestValidateProfile:
    def test_clean_profile(self) -> None:
        p = get_builtin_profile("frontend")
        issues = validate_profile(p)
        assert issues == []

    def test_contradictory_read_write(self) -> None:
        p = SandboxProfile(
            name="conflict",
            fs_rules=(
                FileSystemRule(path="/data", permissions="read"),
                FileSystemRule(path="/data", permissions="write"),
            ),
        )
        issues = validate_profile(p)
        kinds = [i.kind for i in issues]
        assert "contradictory_fs" in kinds

    def test_contradictory_read_execute(self) -> None:
        p = SandboxProfile(
            name="conflict",
            fs_rules=(
                FileSystemRule(path="/bin", permissions="read"),
                FileSystemRule(path="/bin", permissions="execute"),
            ),
        )
        issues = validate_profile(p)
        kinds = [i.kind for i in issues]
        assert "contradictory_fs" in kinds

    def test_wildcard_network_flagged(self) -> None:
        p = SandboxProfile(
            name="wide",
            network_rules=(NetworkRule(host="0.0.0.0", port=0),),
        )
        issues = validate_profile(p)
        kinds = [i.kind for i in issues]
        assert "wildcard_network" in kinds

    def test_empty_profile_flagged(self) -> None:
        p = SandboxProfile(name="empty")
        issues = validate_profile(p)
        kinds = [i.kind for i in issues]
        assert "empty_profile" in kinds

    def test_write_execute_no_conflict(self) -> None:
        """Write + execute on same path is not contradictory."""
        p = SandboxProfile(
            name="ok",
            fs_rules=(
                FileSystemRule(path="/app", permissions="write"),
                FileSystemRule(path="/app", permissions="execute"),
            ),
        )
        issues = validate_profile(p)
        assert issues == []

    def test_different_paths_no_conflict(self) -> None:
        """Read and write on different paths is fine."""
        p = SandboxProfile(
            name="ok",
            fs_rules=(
                FileSystemRule(path="/data", permissions="read"),
                FileSystemRule(path="/tmp", permissions="write"),
            ),
        )
        issues = validate_profile(p)
        assert issues == []

    def test_profile_conflict_is_frozen(self) -> None:
        c = ProfileConflict(kind="test", message="msg")
        with pytest.raises(AttributeError):
            c.kind = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# render_profile_summary
# ---------------------------------------------------------------------------


class TestRenderProfileSummary:
    def test_contains_name(self) -> None:
        p = get_builtin_profile("web-backend")
        md = render_profile_summary(p)
        assert "web-backend" in md

    def test_contains_network_rules(self) -> None:
        p = get_builtin_profile("web-backend")
        md = render_profile_summary(p)
        assert "5432" in md
        assert "6379" in md

    def test_contains_fs_rules(self) -> None:
        p = get_builtin_profile("web-backend")
        md = render_profile_summary(p)
        assert "/app" in md

    def test_contains_env_vars(self) -> None:
        p = get_builtin_profile("web-backend")
        md = render_profile_summary(p)
        assert "DATABASE_URL" in md

    def test_contains_commands(self) -> None:
        p = get_builtin_profile("web-backend")
        md = render_profile_summary(p)
        assert "python" in md

    def test_minimal_shows_no_access(self) -> None:
        p = get_builtin_profile("minimal")
        md = render_profile_summary(p)
        assert "No network access" in md
        assert "None" in md

    def test_wildcard_port_rendered(self) -> None:
        p = SandboxProfile(
            name="wide",
            network_rules=(NetworkRule(host="0.0.0.0", port=0),),
        )
        md = render_profile_summary(p)
        assert "all ports" in md

    def test_output_is_markdown(self) -> None:
        p = get_builtin_profile("ci-runner")
        md = render_profile_summary(p)
        assert md.startswith("# Sandbox Profile:")
        assert "## Network Rules" in md
        assert "## Filesystem Rules" in md
        assert "## Environment Variables" in md
        assert "## Allowed Commands" in md
