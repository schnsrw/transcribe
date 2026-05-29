from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

import yaml

from .types import ChunkedASR, NativeStreamingASR


@dataclass
class BackendBinding:
    name: str
    kind: str                            # "native_streaming" | "chunked"
    instance: NativeStreamingASR | ChunkedASR
    config: dict[str, Any]


class Router:
    def __init__(self, cfg: dict[str, Any]):
        self._cfg = cfg
        self._routes: dict[str, str] = cfg.get("routes", {})
        self._backends: dict[str, BackendBinding] = {}

    def load_backends(self) -> None:
        """Instantiate every backend referenced by `routes`."""
        names = {b for b in self._routes.values()}
        for name in names:
            bcfg = self._cfg["backends"][name]
            module_path, class_name = bcfg["module"].split(":")
            cls = getattr(importlib.import_module(module_path), class_name)
            instance = cls(bcfg)
            self._backends[name] = BackendBinding(
                name=name, kind=bcfg["kind"], instance=instance, config=bcfg,
            )

    def for_language(self, lang: str) -> BackendBinding:
        name = self._routes.get(lang) or self._routes.get("*")
        if name is None:
            raise KeyError(f"no backend route for lang={lang!r} and no '*' fallback")
        return self._backends[name]


def load_config(path: str) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)
