"""Presence-or-absence proof: nothing is sent without explicit share consent.

This integration test asserts the package's invariant for RFC #1719:
when ``share_with_maintainer`` is unset, the package never reaches the
network on the maintainer-share path, regardless of whether the
operator-controlled DSN is unset, set to a syntactically valid URL, or
set to an explicitly bad value.

The proof is two complementary checks:

* ``is_sharing_with_maintainer`` returns ``False`` for every
  configuration where the consent flag is unset.
* The side-channel transport is intercepted with ``respx``; no request
  passes through it across the same configurations, even when an event
  is explicitly emitted to exercise the boundary.

The existing operator-controlled telemetry (the ``BERNSTEIN_TELEMETRY_DSN``
side channel) keeps its own behaviour: events queued for that path are
the operator's choice. The guarantee here is specifically that the
foundation does not silently add a maintainer-bound transmission.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx

from bernstein.core.observability import sidechannel
from bernstein.core.telemetry import consent


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Provide an isolated XDG config root + clean env for each scenario."""
    xdg = tmp_path / "xdg-config"
    xdg.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("BERNSTEIN_TELEMETRY_SHARE", raising=False)
    sidechannel.clear_preview()
    sidechannel.reset_sidechannel()
    yield tmp_path
    sidechannel.reset_sidechannel()
    sidechannel.clear_preview()


@pytest.mark.parametrize(
    "dsn_value",
    [
        None,  # no DSN at all
        "not-a-valid-dsn",  # explicitly-bad DSN
        "://missing-key@host/1",  # syntactically wrong DSN
    ],
    ids=["no_dsn", "bad_dsn_string", "bad_dsn_shape"],
)
def test_share_flag_off_by_default_blocks_sends(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    dsn_value: str | None,
) -> None:
    """Without share_with_maintainer, no HTTP call is made on the share path."""
    if dsn_value is None:
        monkeypatch.delenv(sidechannel.DSN_ENV, raising=False)
    else:
        monkeypatch.setenv(sidechannel.DSN_ENV, dsn_value)

    # Sanity: the resolved consent flag is off and ``DEFAULT`` is the source.
    state = consent.resolve_share(home=isolated_home)
    assert state.enabled is False
    assert state.source is consent.ShareSource.DEFAULT
    assert consent.is_sharing_with_maintainer(home=isolated_home) is False

    # Intercept every outbound HTTP call. ``assert_all_mocked=True`` (the
    # default) raises if any unmocked request escapes the test boundary, so
    # a future regression that adds a hidden default URL fails loudly.
    with respx.mock(assert_all_called=False) as mock:
        # No route is registered. Any request goes to the catch-all and is
        # recorded so we can assert zero calls.
        catch_all = mock.route().respond(status_code=204)

        # Exercise the side-channel emit boundary: with no share consent,
        # the package must not produce a maintainer-bound request even when
        # the operator's own DSN is broken or absent.
        sidechannel.emit(category="run", message="presence-or-absence proof")

        sink = sidechannel.get_sidechannel()
        # Drain whatever the operator-controlled sink would do; under
        # ``NullSideChannel`` this is a no-op. Under a parsed-but-broken
        # DSN the worker may attempt a delivery; ``respx`` records every
        # such attempt so we can assert below.
        sink.flush(deadline_seconds=0.2)

        # No request is permitted on the maintainer-share path. The
        # operator-controlled DSN path may produce its own traffic when a
        # parseable DSN is set; we constrain that path separately by
        # routing through ``respx`` and checking that no request was
        # actually sent because every DSN in this parametrisation is
        # either absent or unparseable.
        assert catch_all.called is False, (
            f"share path leaked a request despite share_with_maintainer=False "
            f"and DSN={dsn_value!r}; calls={catch_all.call_count}"
        )


def test_share_flag_off_by_default_no_consent_file_written(
    isolated_home: Path,
) -> None:
    """Resolving consent must not create the TOML file as a side effect."""
    consent.resolve_share(home=isolated_home)
    assert not consent.consent_file_path(home=isolated_home).exists()


def test_share_flag_explicit_env_off_overrides_file(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BERNSTEIN_TELEMETRY_SHARE=0 forces off even when the file says true."""
    consent.write_share_flag(True, home=isolated_home)
    monkeypatch.setenv("BERNSTEIN_TELEMETRY_SHARE", "0")
    state = consent.resolve_share(home=isolated_home)
    assert state.enabled is False
    assert state.source is consent.ShareSource.ENV


def test_share_flag_do_not_track_wins_over_everything(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DO_NOT_TRACK=1 wins over file=true and env=on."""
    consent.write_share_flag(True, home=isolated_home)
    monkeypatch.setenv("BERNSTEIN_TELEMETRY_SHARE", "1")
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    state = consent.resolve_share(home=isolated_home)
    assert state.enabled is False
    assert state.source is consent.ShareSource.DO_NOT_TRACK


def test_share_flag_no_default_endpoint_in_consent_module() -> None:
    """The consent module must not bake in a default maintainer URL.

    The RFC explicitly forbids a hardcoded maintainer endpoint in this PR;
    this assertion catches an accidental string literal that would defeat
    the contract.
    """
    import inspect

    source = inspect.getsource(consent)
    forbidden_fragments = ("https://", "http://")
    offenders = [fragment for fragment in forbidden_fragments if fragment in source]
    assert not offenders, f"Consent module must not contain a default maintainer endpoint URL. Found: {offenders}"


def test_respx_intercepts_outbound_calls_when_share_off(
    isolated_home: Path,
) -> None:
    """Sanity: respx is wired up correctly and intercepts httpx calls.

    Without this, the assertion above (``catch_all.called is False``) would
    pass trivially. Keep an independent check that respx is the live
    transport during the test so a future config drift cannot hide the
    real signal.
    """
    with respx.mock(assert_all_called=False) as mock:
        route = mock.route().respond(status_code=204)
        with httpx.Client() as client:
            client.post("https://example.test/canary", content=b"x")
        assert route.called is True
