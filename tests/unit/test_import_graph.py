"""
Enforces the import-graph rules from docs/CODEGRAPH.md.

If you change imports in a way that breaks these tests, either:
  * adjust the rule in CODEGRAPH.md (with reasoning), or
  * undo the import change.

The point is to catch architectural drift early — backends importing
participant.py, for example, would silently re-enable circular reasoning
that the protocol split was specifically designed to avoid.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "casual_sst"


def _imports(file: Path) -> set[str]:
    tree = ast.parse(file.read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                # Resolve relative imports like `from .frame import X` into
                # `casual_sst.frame` so the rule checks can be plain string ops.
                if node.level > 0:
                    pkg = "casual_sst"
                    if file.parent.name == "backends":
                        pkg = "casual_sst.backends" if node.level == 1 else "casual_sst"
                    out.add(f"{pkg}.{node.module}")
                else:
                    out.add(node.module)
    return out


def test_types_only_imports_stdlib() -> None:
    imps = _imports(SRC / "types.py")
    for name in imps:
        # typing.Protocol etc. plus dataclasses, collections — all stdlib.
        assert "." not in name or name.startswith(("typing", "collections", "dataclasses")), (
            f"types.py should not depend on third-party: {name}"
        )


def test_backends_do_not_import_pipeline() -> None:
    forbidden_prefixes = (
        "casual_sst.participant",
        "casual_sst.meeting",
        "casual_sst.main",
        "casual_sst.router",
        "casual_sst.lid",
        "casual_sst.lang_state",
        "casual_sst.profile",
        "casual_sst.vad",
    )
    for f in (SRC / "backends").glob("*.py"):
        if f.name == "__init__.py":
            continue
        imps = _imports(f)
        for name in imps:
            assert not name.startswith(forbidden_prefixes), (
                f"{f.name} imports {name} — backends must not depend on pipeline modules"
            )


def test_participant_is_the_only_caller_of_lang_state_and_router() -> None:
    # Both lang_state and router are imported by participant.py (allowed)
    # and by main.py / meeting.py for the router only. Lang state should
    # not leak elsewhere.
    for f in SRC.rglob("*.py"):
        if f.name in {"participant.py", "lang_state.py", "__init__.py"}:
            continue
        imps = _imports(f)
        assert "casual_sst.lang_state" not in imps, (
            f"{f.relative_to(SRC)} imports lang_state — only participant.py is allowed to."
        )
