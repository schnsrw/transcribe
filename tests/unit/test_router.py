"""
Unit tests for ``casual_sst.router.load_config``.

Focus: the ``extends:`` chain and deep-merge semantics. The pipeline trusts
the merged config to be coherent, so this is the most "load-bearing" piece
of the loader.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from casual_sst.router import _deep_merge, load_config


def test_deep_merge_overrides_scalars() -> None:
    base = {"a": 1, "b": 2}
    out = _deep_merge(base, {"b": 99})
    assert out == {"a": 1, "b": 99}


def test_deep_merge_recurses_into_dicts() -> None:
    base = {"server": {"port": 8000, "host": "0.0.0.0"}}
    out = _deep_merge(base, {"server": {"port": 9000}})
    assert out == {"server": {"port": 9000, "host": "0.0.0.0"}}


def test_deep_merge_replaces_lists() -> None:
    # Lists are not concatenated — see docstring on load_config().
    base = {"x": [1, 2, 3]}
    out = _deep_merge(base, {"x": [9]})
    assert out == {"x": [9]}


def test_deep_merge_preserves_base_only_keys() -> None:
    base = {"a": {"x": 1, "y": 2}}
    out = _deep_merge(base, {"a": {"y": 99}})
    assert out == {"a": {"x": 1, "y": 99}}


def test_load_config_with_extends(tmp_path: Path) -> None:
    base = tmp_path / "base.yaml"
    base.write_text(yaml.safe_dump({"server": {"port": 8000, "host": "0.0.0.0"}, "routes": {"en": "X"}}))
    env = tmp_path / "local.yaml"
    env.write_text(yaml.safe_dump({"extends": "base.yaml", "server": {"port": 9000}}))

    cfg = load_config(str(env))
    assert cfg["server"] == {"port": 9000, "host": "0.0.0.0"}
    assert cfg["routes"] == {"en": "X"}
    assert "extends" not in cfg


def test_load_config_without_extends_returns_as_is(tmp_path: Path) -> None:
    p = tmp_path / "standalone.yaml"
    p.write_text(yaml.safe_dump({"foo": "bar"}))
    assert load_config(str(p)) == {"foo": "bar"}
