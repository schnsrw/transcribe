"""
casual_sst.router
=================

Configuration loading and backend routing.

The router is the only place that knows how to:
  1. Read a YAML config file with an optional ``extends:`` chain
     (``config/local.yaml`` / ``config/prod.yaml`` both extend ``base.yaml``).
  2. Recursively deep-merge an environment override on top of a base config.
  3. Instantiate exactly the set of backends referenced by ``routes:`` and
     hand them out by language code.

Design notes:
  * Backends are *instantiated lazily but loaded eagerly* — ``load_backends``
    constructs every referenced backend at startup so the server fails fast
    if a backend (e.g. a missing model file) is broken. There is no silent
    fallback chain — see ADR-008.
  * The router holds the resolved config dict and uses it as the single
    source of truth for thresholds, deny-lists and tuning knobs. Pipeline
    code reads ``cfg`` directly; it never imports module-level constants
    that duplicate config.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from typing import Any

import yaml

from .types import ChunkedASR, NativeStreamingASR


@dataclass
class BackendBinding:
    """A resolved backend ready for use by the pipeline.

    Attributes
    ----------
    name : str
        Logical name (e.g. ``"whisper_turbo"``) — matches the YAML key.
    kind : str
        Either ``"native_streaming"`` or ``"chunked"``. Drives the
        pipeline branch in ``casual_sst.participant``.
    instance : NativeStreamingASR | ChunkedASR
        The constructed backend object.
    config : dict
        The backend's slice of the YAML config (post-merge), kept so
        downstream code can read per-backend overrides without
        re-traversing the global config.
    """
    name: str
    kind: str
    instance: NativeStreamingASR | ChunkedASR
    config: dict[str, Any]


class Router:
    """Looks up the backend responsible for a given language code."""

    def __init__(self, cfg: dict[str, Any]):
        self._cfg = cfg
        self._routes: dict[str, str] = cfg.get("routes", {})
        self._backends: dict[str, BackendBinding] = {}

    def load_backends(self) -> None:
        """Instantiate every backend referenced by ``routes:``.

        Backends not referenced are skipped entirely (no import, no model
        load). This keeps local-dev startup fast: ``config/local.yaml``
        routes every language to ``whisper_turbo`` so the Voxtral /
        Parakeet / IndicConformer stubs never get touched.
        """
        names = {b for b in self._routes.values()}
        for name in names:
            bcfg = self._cfg["backends"][name]
            module_path, class_name = bcfg["module"].split(":")
            cls = getattr(importlib.import_module(module_path), class_name)
            instance = cls(bcfg)
            self._backends[name] = BackendBinding(
                name=name,
                kind=bcfg["kind"],
                instance=instance,
                config=bcfg,
            )

    def for_language(self, lang: str) -> BackendBinding:
        """Return the backend binding for ``lang``, falling back to ``"*"``.

        Raises
        ------
        KeyError
            If there is no route for ``lang`` and no ``"*"`` fallback.
        """
        name = self._routes.get(lang) or self._routes.get("*")
        if name is None:
            raise KeyError(f"no backend route for lang={lang!r} and no '*' fallback")
        return self._backends[name]


def load_config(path: str) -> dict[str, Any]:
    """Load a YAML config file, transparently resolving an ``extends:`` chain.

    A non-base config can declare ``extends: <relative path>`` at the top
    level. The base is read first, the override file is read second, and
    the two are recursively deep-merged (override wins on conflicting keys;
    nested dicts are merged in place rather than replaced wholesale).

    Lists are replaced, not concatenated — this matches user expectation
    for things like ``hallucination.denylist.en`` where an env-specific
    config wanting a different list should not silently inherit the base.
    """
    cfg = _read_yaml(path)
    extends = cfg.pop("extends", None)
    if not extends:
        return cfg
    base_path = os.path.join(os.path.dirname(os.path.abspath(path)), extends)
    base = _read_yaml(base_path)
    return _deep_merge(base, cfg)


def _read_yaml(path: str) -> dict[str, Any]:
    with open(path) as f:
        loaded = yaml.safe_load(f)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f"config root must be a mapping: {path}")
    return loaded


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge. ``override`` wins; nested dicts merged in place."""
    out: dict[str, Any] = dict(base)
    for key, value in override.items():
        if (
            key in out
            and isinstance(out[key], dict)
            and isinstance(value, dict)
        ):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out
