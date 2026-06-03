"""`cards-runner doctor` -- diagnostic dump for the runner.

Both the chunk-3 and chunk-4 handoffs named `doctor` as a deferred
follow-up. Chunk 6c finally lands it.

The subcommand answers four questions an operator routinely asks before
filing a bug or paging the runner owner:

1. **Which binaries did the runner resolve?** `gh`, `git`, `dolt` --
   the runner's defaults assume each is on PATH, but operators
   override via env vars (`CARDS_DOLT_BIN`) or CLI flags (`--gh`,
   `--git`). Doctor reports the live resolved path plus the version
   each binary self-reports.
2. **Where is the project config and what's in it?** The project YAML
   shapes reviewer behavior, merge gate relaxation, story-drift
   defaults; printing the resolved path + a one-line summary of each
   field saves a "wait, which project.yaml is this picking up?"
   round-trip.
3. **What schema migrations have been applied?** `schema.ADDED_COLUMNS`
   is the authoritative list of post-initial-create migrations.
   Doctor introspects the live `cards` table and reports which are
   present + which would be added by a fresh `initialize_schema()`
   call.
4. **What are the chunk-4/5/6 knobs set to?** `DaemonConfig` carries
   ~15 toggles -- `pr_gate_enabled`, `pr_unblock_enabled`,
   `sibling_reviewer_enabled`, etc. Doctor prints them as a
   per-knob "on / off / default" table so an operator can verify
   their flag actually flipped.

Output formats: plain text (default, human-readable) and JSON
(`--json`, machine-readable for CI / monitoring). Both are produced
from the same `DoctorReport` dataclass so the schema is one place.

Doctor reads from disk only; it does NOT shell `gh` to make network
calls, NOT spawn a daemon, NOT mutate any state. Safe to run while
a daemon is live.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..common.project_config import (
    ProjectConfig,
    resolve_project_config_path,
    load_project_config,
)
from ..common.types import DaemonConfig, RuntimePaths
from ..store.repository import CardRepository
from ..store.schema import ADDED_COLUMNS, EXPECTED_TABLES


@dataclass(frozen=True)
class BinaryReport:
    """One resolved binary's presence + version."""

    name: str          # "gh", "git", "dolt"
    requested: str     # what the runner asked for ("gh", "C:\\path\\dolt.exe")
    resolved_path: str | None  # what shutil.which returned, or None
    version: str | None        # first line of `<bin> --version`
    error: str = ""            # populated when version check failed


@dataclass(frozen=True)
class SchemaReport:
    """Per-`ADDED_COLUMNS` entry: applied or pending."""

    table: str
    column: str
    applied: bool
    sqlite_type: str
    mysql_type: str


@dataclass(frozen=True)
class TableReport:
    """Per-`EXPECTED_TABLES` entry: present in the live database or not.

    ADDED_COLUMNS only covers post-create migrations on existing tables.
    Whole new tables (the ledger-chunk-1 `card_metrics` and
    `metric_estimates`) need their own presence check so an operator can
    confirm `initialize_schema()` actually ran end-to-end.
    """

    name: str
    present: bool


@dataclass(frozen=True)
class ProjectConfigReport:
    """Resolved project.yaml path + the values it actually loaded."""

    path: str | None
    exists: bool
    source: str           # "explicit" / "todo_root_default" / "missing"
    story_source_path: str | None
    sibling_reviewer_enabled: bool
    sibling_reviewer_model: str
    amendment_reviewer_enabled: bool
    amendment_reviewer_auto_edit_ac: bool
    amendment_reviewer_model: str
    merge_gate_auto_merge_tier_3_4: bool


@dataclass(frozen=True)
class KnobReport:
    """One DaemonConfig knob, with its current value + default."""

    name: str
    value: Any
    is_default: bool


@dataclass(frozen=True)
class DoctorReport:
    todo_root: str
    store_spec: str
    binaries: list[BinaryReport]
    project_config: ProjectConfigReport
    schema: list[SchemaReport]
    tables: list[TableReport]
    knobs: list[KnobReport]
    notes: list[str] = field(default_factory=list)


# The DaemonConfig fields doctor reports on. We don't dump every field
# (poll_interval_sec, max_parallel etc. are noise); just the chunk-4
# / chunk-5 / chunk-6 knobs an operator cares about. Field name +
# default; doctor compares the live value to the default to mark
# explicit overrides.
_KNOB_DEFAULTS: dict[str, Any] = {
    "verifier_enabled": True,
    "verifier_cascade_disabled": False,
    "pr_gate_enabled": False,
    "pr_unblock_enabled": False,
    "sibling_reviewer_enabled": False,
    "amendment_reviewer_enabled": False,
    "worktree_prune_enabled": False,
    "boot_worker_alive_check": True,
    "skip_worktree": False,
    "invoker": "stub",
    "auto_merge_strategy": "squash",
}


def build_report(
    cfg: DaemonConfig,
    *,
    repo: CardRepository | None = None,
    dolt_bin_env: str | None = None,
) -> DoctorReport:
    """Compose a `DoctorReport` from a DaemonConfig + an open repo.

    `repo` is optional: when None, the schema section reports an empty
    list of applied columns and a note explains that the store was not
    introspected. CLI callers pass the open repo so the section is
    real; tests can pass None to skip the store dependency.
    """
    notes: list[str] = []
    binaries = _binary_reports(cfg, dolt_bin_env=dolt_bin_env)
    project = _project_config_report(cfg)
    schema = _schema_report(repo, notes=notes)
    tables = _table_report(repo, notes=notes)
    knobs = _knob_reports(cfg)
    return DoctorReport(
        todo_root=str(cfg.todo_root),
        store_spec=cfg.resolved_store_spec(),
        binaries=binaries,
        project_config=project,
        schema=schema,
        tables=tables,
        knobs=knobs,
        notes=notes,
    )


def render_text(report: DoctorReport) -> str:
    """Plain-text rendering for human consumption."""
    lines: list[str] = []
    lines.append(f"todo_root: {report.todo_root}")
    lines.append(f"store: {report.store_spec}")
    lines.append("")
    lines.append("binaries:")
    for b in report.binaries:
        version = b.version if b.version else "?"
        path = b.resolved_path if b.resolved_path else "(not found)"
        suffix = f" -- {b.error}" if b.error else ""
        lines.append(f"  {b.name}: {path}  [{version}]{suffix}")
    lines.append("")
    pc = report.project_config
    lines.append("project config:")
    lines.append(f"  path: {pc.path or '(none)'}")
    lines.append(f"  exists: {pc.exists}")
    lines.append(f"  source: {pc.source}")
    lines.append(f"  story_source_path: {pc.story_source_path or '(unset)'}")
    lines.append(
        f"  sibling_reviewer: enabled={pc.sibling_reviewer_enabled} "
        f"model={pc.sibling_reviewer_model}"
    )
    lines.append(
        f"  amendment_reviewer: enabled={pc.amendment_reviewer_enabled} "
        f"auto_edit_ac={pc.amendment_reviewer_auto_edit_ac} "
        f"model={pc.amendment_reviewer_model}"
    )
    lines.append(
        f"  merge_gate.auto_merge_tier_3_4: {pc.merge_gate_auto_merge_tier_3_4}"
    )
    lines.append("")
    lines.append("schema migrations:")
    if not report.schema:
        lines.append("  (no introspection -- pass --check-store to enable)")
    else:
        for s in report.schema:
            status = "applied" if s.applied else "PENDING"
            lines.append(f"  {s.table}.{s.column}: {status}")
    lines.append("")
    lines.append("tables:")
    if not report.tables:
        lines.append("  (no introspection -- pass --check-store to enable)")
    else:
        for t in report.tables:
            status = "present" if t.present else "MISSING"
            lines.append(f"  {t.name}: {status}")
    lines.append("")
    lines.append("knobs:")
    for k in report.knobs:
        marker = "" if k.is_default else " (overridden)"
        lines.append(f"  {k.name}: {k.value}{marker}")
    if report.notes:
        lines.append("")
        lines.append("notes:")
        for note in report.notes:
            lines.append(f"  - {note}")
    return "\n".join(lines) + "\n"


def render_json(report: DoctorReport) -> str:
    return json.dumps(asdict(report), indent=2, sort_keys=True)


# ---- internals ------------------------------------------------------


def _binary_reports(
    cfg: DaemonConfig, *, dolt_bin_env: str | None
) -> list[BinaryReport]:
    out: list[BinaryReport] = []
    # gh
    requested_gh = cfg.gh_path
    out.append(_binary_report("gh", requested_gh, ("--version",)))
    # git
    requested_git = cfg.git_path
    out.append(_binary_report("git", requested_git, ("--version",)))
    # dolt
    # The runner reads CARDS_DOLT_BIN at store-build time; doctor mirrors
    # the same resolution so an operator sees what the daemon would pick.
    dolt_request = dolt_bin_env or "dolt"
    out.append(_binary_report("dolt", dolt_request, ("version",)))
    return out


def _binary_report(
    name: str, requested: str, version_args: tuple[str, ...],
) -> BinaryReport:
    resolved = shutil.which(requested)
    if resolved is None and Path(requested).is_file():
        resolved = str(Path(requested).resolve())
    if resolved is None:
        return BinaryReport(
            name=name, requested=requested,
            resolved_path=None, version=None,
            error="not found on PATH",
        )
    version_line, err = _safe_version(resolved, version_args)
    return BinaryReport(
        name=name, requested=requested,
        resolved_path=resolved, version=version_line, error=err,
    )


def _safe_version(
    binary: str, args: tuple[str, ...]
) -> tuple[str | None, str]:
    """Run `<binary> <args>` with a tight timeout; return (first line, err)."""
    try:
        cp = subprocess.run(
            [binary, *args],
            capture_output=True, text=True, timeout=5.0, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return (None, f"version check failed: {exc}")
    text = (cp.stdout or cp.stderr or "").strip()
    if not text:
        return (None, "version check returned no output")
    first_line = text.splitlines()[0]
    return (first_line, "")


def _project_config_report(cfg: DaemonConfig) -> ProjectConfigReport:
    paths = RuntimePaths.from_root(cfg.todo_root)
    resolved = resolve_project_config_path(
        cfg.project_config_path, todo_root=paths.todo_root,
    )
    if resolved is None:
        return ProjectConfigReport(
            path=None, exists=False, source="missing",
            story_source_path=None,
            sibling_reviewer_enabled=False,
            sibling_reviewer_model=ProjectConfig.default().sibling_reviewer.model_id,
            amendment_reviewer_enabled=False,
            amendment_reviewer_auto_edit_ac=False,
            amendment_reviewer_model=ProjectConfig.default().amendment_reviewer.model_id,
            merge_gate_auto_merge_tier_3_4=False,
        )
    source = "explicit" if cfg.project_config_path else "todo_root_default"
    exists = resolved.is_file()
    project = load_project_config(resolved) if exists else ProjectConfig.default()
    return ProjectConfigReport(
        path=str(resolved),
        exists=exists,
        source=source,
        story_source_path=project.story_source_path,
        sibling_reviewer_enabled=project.sibling_reviewer.enabled,
        sibling_reviewer_model=project.sibling_reviewer.model_id,
        amendment_reviewer_enabled=project.amendment_reviewer.enabled,
        amendment_reviewer_auto_edit_ac=project.amendment_reviewer.auto_edit_ac,
        amendment_reviewer_model=project.amendment_reviewer.model_id,
        merge_gate_auto_merge_tier_3_4=project.merge_gate.auto_merge_tier_3_4,
    )


def _schema_report(
    repo: CardRepository | None, *, notes: list[str],
) -> list[SchemaReport]:
    if repo is None:
        notes.append(
            "schema section skipped: open the store with --check-store to "
            "introspect ADDED_COLUMNS"
        )
        return []
    live_columns: set[str] = _live_card_columns(repo, notes=notes)
    return [
        SchemaReport(
            table=table, column=column,
            applied=column in live_columns,
            sqlite_type=sqlite_type, mysql_type=mysql_type,
        )
        for table, column, sqlite_type, mysql_type in ADDED_COLUMNS
    ]


def _live_card_columns(
    repo: CardRepository, *, notes: list[str],
) -> set[str]:
    """Introspect the `cards` table's live column set.

    Tries the SQLite path first (`PRAGMA table_info`), falls back to
    `DESCRIBE cards` for MySQL-backed Dolt. A repo without a public
    SQL handle returns the empty set + a note.
    """
    conn = getattr(repo, "_conn", None) or getattr(repo, "conn", None)
    if conn is None:
        notes.append(
            "schema introspection unavailable: repo has no SQL connection "
            "handle; ADDED_COLUMNS reported as PENDING"
        )
        return set()
    # SQLite first.
    try:
        cur = conn.execute("PRAGMA table_info(cards)")
        rows = cur.fetchall()
        names = {row[1] for row in rows} if rows else set()
        if names:
            return names
    except Exception:  # noqa: BLE001 - fall through to MySQL.
        pass
    try:
        cur = conn.execute("DESCRIBE cards")
        return {row[0] for row in cur.fetchall()}
    except Exception as exc:  # noqa: BLE001 - the store dialect is unsupported.
        notes.append(f"schema introspection failed: {exc}")
        return set()


def _table_report(
    repo: CardRepository | None, *, notes: list[str],
) -> list[TableReport]:
    """Report presence/absence for every table in `EXPECTED_TABLES`."""
    if repo is None:
        # `_schema_report` already added the "open the store" note; no
        # need to duplicate it here.
        return []
    live = _live_tables(repo, notes=notes)
    return [TableReport(name=name, present=name in live) for name in EXPECTED_TABLES]


def _live_tables(
    repo: CardRepository, *, notes: list[str],
) -> set[str]:
    """Introspect the database's live table list.

    Same dual-path the column check uses: SQLite via `sqlite_master`,
    MySQL/Dolt via `information_schema.tables`. A repo without an
    accessible connection returns the empty set + a note.
    """
    conn = getattr(repo, "_conn", None) or getattr(repo, "conn", None)
    if conn is None:
        notes.append(
            "table introspection unavailable: repo has no SQL connection "
            "handle; all EXPECTED_TABLES reported as MISSING"
        )
        return set()
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        names = {row[0] for row in cur.fetchall()}
        if names:
            return names
    except Exception:  # noqa: BLE001 - fall through to MySQL.
        pass
    try:
        cur = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = DATABASE()"
        )
        return {row[0] for row in cur.fetchall()}
    except Exception as exc:  # noqa: BLE001 - unsupported dialect.
        notes.append(f"table introspection failed: {exc}")
        return set()


def _knob_reports(cfg: DaemonConfig) -> list[KnobReport]:
    return [
        KnobReport(
            name=name,
            value=getattr(cfg, name),
            is_default=getattr(cfg, name) == default,
        )
        for name, default in _KNOB_DEFAULTS.items()
    ]
