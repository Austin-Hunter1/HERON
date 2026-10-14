"""
`utils.environment_variables`: one `.env` serving several machines.

The module applies its overrides once, at import, to the real environment, so these
tests call `apply_platform_overrides` directly with plain dicts and an explicit
platform name rather than touching `os.environ`.
"""

from __future__ import annotations

import pytest

from utils import environment_variables as env

WIN = "D:/01_Data/collects"
LIN = "/media/austin/PhD_Workspace/01_Data/collects"


def apply(environ, shell=None, platform="win32", **kwargs):
    env.apply_platform_overrides(environ, shell or {}, platform=platform, **kwargs)
    return environ


@pytest.mark.parametrize(
    "platform, expected",
    [("win32", "WINDOWS"), ("linux", "LINUX"), ("darwin", "MACOS"), ("cygwin", None), ("freebsd13", None)],
)
def test_platform_suffix(platform, expected):
    assert env.platform_suffix(platform) == expected


def test_the_key_for_this_platform_wins_over_the_plain_key():
    environ = {"COLLECTS_PATH": "/plain", "COLLECTS_PATH_WINDOWS": WIN, "COLLECTS_PATH_LINUX": LIN}
    assert apply(dict(environ), platform="win32")["COLLECTS_PATH"] == WIN
    assert apply(dict(environ), platform="linux")["COLLECTS_PATH"] == LIN


def test_the_plain_key_is_used_when_no_key_matches_this_platform():
    environ = {"COLLECTS_PATH": "/plain", "COLLECTS_PATH_LINUX": LIN}
    assert apply(environ, platform="win32")["COLLECTS_PATH"] == "/plain"


def test_a_platform_key_alone_sets_the_variable():
    assert apply({"COLLECTS_PATH_WINDOWS": WIN}, platform="win32")["COLLECTS_PATH"] == WIN


def test_a_variable_set_in_the_shell_always_wins():
    environ = {"COLLECTS_PATH": "/from-the-shell", "COLLECTS_PATH_WINDOWS": WIN}
    shell = {"COLLECTS_PATH": "/from-the-shell"}
    assert apply(environ, shell, platform="win32")["COLLECTS_PATH"] == "/from-the-shell"


def test_an_empty_platform_key_is_ignored():
    environ = {"COLLECTS_PATH": "/plain", "COLLECTS_PATH_WINDOWS": ""}
    assert apply(environ, platform="win32")["COLLECTS_PATH"] == "/plain"


def test_each_variable_is_resolved_on_its_own():
    environ = {
        "COLLECTS_PATH_WINDOWS": WIN,
        "OUTPUTS_PATH": "/shared/outputs",
        "RESOURCES_PATH_WINDOWS": "D:/01_Data/resources",
    }
    apply(environ, platform="win32")
    assert environ["COLLECTS_PATH"] == WIN
    assert environ["OUTPUTS_PATH"] == "/shared/outputs"
    assert environ["RESOURCES_PATH"] == "D:/01_Data/resources"


def test_credentials_are_never_platform_dependent():
    environ = {"EARTHDATA_USERNAME": "plain", "EARTHDATA_USERNAME_WINDOWS": "other"}
    assert apply(environ, platform="win32")["EARTHDATA_USERNAME"] == "plain"


def test_an_unknown_platform_changes_nothing():
    environ = {"COLLECTS_PATH": "/plain", "COLLECTS_PATH_WINDOWS": WIN}
    assert apply(environ, platform="cygwin")["COLLECTS_PATH"] == "/plain"
