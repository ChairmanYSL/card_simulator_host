"""Case library loader: scans the cases directory, validates JSONs, indexes.

Layout (root = ``--cases`` dir)::

    cases/
      <scheme>/
        scheme.json                      scheme metadata + card_template + pos_params
        contactless/cases/<case_id>.json              standalone self-contained case
        contactless/cases/<group_id>/group.json       总案例 (description only, no profile)
        contactless/cases/<group_id>/<group_id>-NN.json  子案例 (self-contained case)

Every executable case is self-contained: ``profile`` (and optional ``keys``)
are embedded in the case JSON and bound to the case (v2 binding); there is no
scheme-level shared profile library any more.  A 总案例 group carries no
profile — only its 子案例 do.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import (
    GroupDef,
    KernelParams,
    SchemeDef,
    TestCase,
)
from .validation import (
    validate_kernel_params,
    validate_keys,
    validate_pos_params,
    validate_profile,
)

logger = logging.getLogger("emv_host.cases")

#: Local (gitignored) per-scheme POS kernel parameter file.  Generated from the
#: POS assets by tools/export_kernel_params.py; the CAPK moduli it contains are
#: key material and must not be committed.
KERNEL_PARAMS_FILE = "kernel_params.json"


class CaseLibraryError(Exception):
    """Fatal library problem (bad root dir, no schemes...)."""


@dataclass
class LoadIssue:
    path: str
    severity: str  # error | warning
    message: str


@dataclass
class CaseLibrary:
    root: Path
    schemes: dict[str, SchemeDef] = field(default_factory=dict)
    cases: dict[tuple[str, str], dict[str, TestCase]] = field(default_factory=dict)
    # (scheme, mode) -> {group_id: GroupDef}; sub-cases carry group_id
    groups: dict[tuple[str, str], dict[str, GroupDef]] = field(default_factory=dict)
    issues: list[LoadIssue] = field(default_factory=list)

    # -- lookups ------------------------------------------------------------
    def scheme_cases(self, scheme: str, mode: str) -> dict[str, TestCase]:
        return self.cases.get((scheme, mode), {})

    def get_case(self, scheme: str, mode: str, case_id: str) -> TestCase | None:
        return self.scheme_cases(scheme, mode).get(case_id)

    def scheme_groups(self, scheme: str, mode: str) -> dict[str, GroupDef]:
        return self.groups.get((scheme, mode), {})

    def get_group(self, scheme: str, mode: str, group_id: str) -> GroupDef | None:
        return self.scheme_groups(scheme, mode).get(group_id)

    def group_child_ids(self, scheme: str, mode: str, group_id: str) -> list[str]:
        return sorted(cid for cid, tc in self.scheme_cases(scheme, mode).items()
                      if tc.group_id == group_id)

    def entry_modes_for(self, scheme: str) -> list[str]:
        scheme_def = self.schemes.get(scheme)
        if scheme_def is None:
            return []
        return [m.value for m in scheme_def.entry_modes]

    def all_case_ids(self, scheme: str, mode: str) -> list[str]:
        return sorted(self.scheme_cases(scheme, mode).keys())

    # -- tree for the web UI ------------------------------------------------
    def tree(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for scheme in sorted(self.schemes):
            scheme_def = self.schemes[scheme]
            modes = []
            for mode in scheme_def.entry_modes:
                mode_cases = self.scheme_cases(scheme, mode.value)
                group_ids = {
                    cid for cid, tc in mode_cases.items() if tc.group_id}
                modes.append({
                    "mode": mode.value,
                    "case_count": len(mode_cases),
                    "cases": [  # all leaf cases (standalone + 子案例)
                        {"case_id": cid, "title": tc.title}
                        for cid, tc in sorted(mode_cases.items())
                    ],
                    "standalone": [  # leaf cases that belong to no group
                        {"case_id": cid, "title": tc.title}
                        for cid, tc in sorted(mode_cases.items())
                        if cid not in group_ids
                    ],
                    "groups": [  # 总案例 groups with their 子案例 children
                        self._group_node(scheme, mode.value, g)
                        for g in sorted(
                            self.scheme_groups(scheme, mode.value).values(),
                            key=lambda x: x.group_id)
                    ],
                })
            out.append({
                "scheme": scheme,
                "display_name": scheme_def.display_name,
                "description": scheme_def.description,
                "case_count": sum(m["case_count"] for m in modes),
                "entry_modes": modes,
            })
        return out

    def _group_node(self, scheme: str, mode: str, group: GroupDef) -> dict[str, Any]:
        children = sorted(
            (tc for tc in self.scheme_cases(scheme, mode).values()
             if tc.group_id == group.group_id),
            key=lambda tc: tc.case_id)
        return {
            "group_id": group.group_id,
            "title": group.title,
            "summary": group.summary,
            "purpose": group.purpose,
            "test_conditions": group.test_conditions,
            "test_plan": [s.model_dump() for s in group.test_plan],
            "pass_criteria": group.pass_criteria,
            "case_count": len(children),
            "children": [{"case_id": tc.case_id, "title": tc.title}
                         for tc in children],
        }

    def public_case(self, scheme: str, mode: str, case_id: str) -> dict[str, Any] | None:
        tc = self.get_case(scheme, mode, case_id)
        if tc is None:
            return None
        profile = tc.profile
        return {
            "case_id": tc.case_id,
            "title": tc.title,
            "summary": tc.summary,
            "scheme": tc.scheme,
            "entry_mode": tc.entry_mode.value,
            "profile": {
                "id": profile.id or tc.case_id,
                "name": profile.name,
                "network": profile.network,
                "acBehavior": profile.acBehavior,
                "ppseMode": profile.ppseMode,
                "ppseSw": profile.ppseSw,
                "ppseData": profile.ppseData,
                "noResponseCommands": [
                    item if isinstance(item, str) else item.command
                    for item in profile.noResponseCommands
                ],
                "aids": [a.aid for a in profile.aids],
                "pan": profile.pan,
                "applicationExpirationDate": profile.applicationExpirationDate,
                "hasKeys": tc.keys is not None,
            },
            "has_keys": tc.keys is not None,
            "preconditions": tc.preconditions,
            "test_plan": [s.model_dump() for s in tc.test_plan],
            "timeout_seconds": tc.timeout_seconds,
            "retry_on_fail": tc.retry_on_fail,
            "operator_instructions": tc.transaction.operator_instructions.model_dump(),
            "pos_params": [p.model_dump() for p in tc.transaction.pos_params],
            "expected_results": [r.model_dump() for r in tc.expected_results],
        }

    def reload(self) -> None:
        """Re-scan the cases directory in place so new files added at runtime
        become visible without restarting the host.  Existing references to
        this instance (e.g. in the runner) keep working."""
        fresh = load_library(self.root)
        self.schemes = fresh.schemes
        self.cases = fresh.cases
        self.groups = fresh.groups
        self.issues = fresh.issues


def load_library(root: str | Path) -> CaseLibrary:
    root = Path(root)
    library = CaseLibrary(root=root)
    if not root.is_dir():
        raise CaseLibraryError(f"cases root does not exist: {root}")

    for scheme_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        scheme_json = scheme_dir / "scheme.json"
        if not scheme_json.is_file():
            library.issues.append(LoadIssue(str(scheme_json), "warning",
                                            "directory has no scheme.json, skipped"))
            continue
        raw_scheme = _safe_read_json(scheme_json)
        if raw_scheme is None:
            library.issues.append(LoadIssue(str(scheme_json), "error",
                                            "scheme.json 无法读取或不是合法 JSON 对象"))
            continue
        try:
            scheme_def = SchemeDef.model_validate(raw_scheme)
        except (ValidationError, json.JSONDecodeError) as exc:
            library.issues.append(LoadIssue(str(scheme_json), "error",
                                            f"invalid scheme.json: {exc}"))
            continue
        scheme = scheme_def.scheme
        # D1: two directories declaring the same scheme key would silently shadow
        # each other (the second wins in library.schemes) — reject the later one.
        if scheme in library.schemes:
            library.issues.append(LoadIssue(
                str(scheme_json), "error",
                f"scheme {scheme!r} 重复定义（同一 cases 根目录下另一个目录已声明），"
                f"该目录被忽略"))
            continue
        library.schemes[scheme] = scheme_def

        # v1.2: scheme-level kernel parameters come from the gitignored local
        # file next to scheme.json (kernel_params.json), never from the
        # committed JSON (it carries CAPK moduli).
        scheme_def.kernel_params = _load_scheme_kernel_params(
            scheme_dir, scheme, library)

        # scheme-level defaults are advisory (clone templates), not shared
        # runnable profiles; validate them but never block the scheme itself.
        if scheme_def.card_template is not None:
            for issue in validate_profile(scheme_def.card_template).issues:
                library.issues.append(LoadIssue(
                    str(scheme_json), issue.severity,
                    f"[card_template:{issue.profile_id or scheme}] {issue.message}"))
        for issue in validate_pos_params(scheme_def.pos_params, scheme).issues:
            library.issues.append(LoadIssue(str(scheme_json), issue.severity,
                                            f"[pos_params] {issue.message}"))
        if scheme_def.kernel_params is not None:
            for issue in validate_kernel_params(
                    scheme_def.kernel_params, f"{scheme}:kernel_params").issues:
                library.issues.append(LoadIssue(
                    str(scheme_dir / KERNEL_PARAMS_FILE), issue.severity,
                    f"[{scheme}] {issue.message}"))

        # cases under each entry-mode dir: flat *.json (standalone) plus
        # sub-directories holding a group (group.json + 子案例 *.json files)
        for mode in scheme_def.entry_modes:
            mode_dir = scheme_dir / mode.value / "cases"
            if not mode_dir.is_dir():
                library.issues.append(LoadIssue(str(mode_dir), "warning",
                                                f"no cases dir for {scheme}/{mode.value}"))
                continue
            mode_cases: dict[str, TestCase] = {}
            mode_groups: dict[str, GroupDef] = {}
            group_paths: dict[str, str] = {}
            # Groups first: every case must be checked against the full set of
            # group_ids in this (scheme, mode) regardless of directory order.
            for item in sorted(mode_dir.iterdir()):
                if item.is_dir():
                    _load_group_dir(item, scheme, mode.value, mode_cases,
                                    mode_groups, group_paths, library)
            # Then cases in ONE deterministic order: standalone files first
            # (empty group key), then each group's children by group id, each
            # set sorted by file name.  So cases/<ID>.json always precedes
            # cases/<GROUP>/<ID>.json and duplicate-id resolution is stable and
            # independent of directory iteration order.
            candidates: list[tuple[tuple[str, str], Path, str | None]] = [
                (("", item.stem), item, None)
                for item in sorted(mode_dir.iterdir())
                if item.is_file() and item.suffix == ".json"]
            for group_id in sorted(mode_groups):
                for case_file in sorted((mode_dir / group_id).glob("*.json")):
                    if case_file.name != "group.json":
                        candidates.append(
                            ((group_id, case_file.stem), case_file, group_id))
            for _key, case_file, owner_group in sorted(candidates,
                                                       key=lambda item: item[0]):
                _load_case_file(case_file, scheme, mode.value, mode_cases,
                                mode_groups, group_paths, library,
                                group_id=owner_group)
            _check_group_case_id_collisions(scheme, mode.value, mode_cases,
                                            mode_groups, group_paths, library)
            library.cases[(scheme, mode.value)] = mode_cases
            library.groups[(scheme, mode.value)] = mode_groups

    if not library.schemes:
        raise CaseLibraryError(f"no valid schemes found under {root}")
    return library


def _load_case_file(case_file: Path, scheme: str, mode: str,
                    mode_cases: dict[str, TestCase],
                    mode_groups: dict[str, GroupDef],
                    group_paths: dict[str, str], library: CaseLibrary,
                    group_id: str | None) -> None:
    # D1: the case_id is also the file stem, so two files with the same stem
    # (e.g. a standalone VISA-CL-001.json and a 子案例 inside a group dir) would
    # silently shadow each other — reject the later one.  Groups load first, so
    # a collision against a group_id is reported as the mutual-exclusion rule.
    if case_file.stem in mode_groups:
        library.issues.append(LoadIssue(
            str(case_file), "error",
            f"case_id {case_file.stem!r} 与同卡组的总案例 group_id 冲突"
            f"（{group_paths.get(case_file.stem, '')}）：case_id 与 group_id 互斥，"
            f"该文件被忽略"))
        return
    if case_file.stem in mode_cases:
        library.issues.append(LoadIssue(
            str(case_file), "error",
            f"case_id {case_file.stem!r} 重复（{scheme}/{mode} 中已存在同号案例："
            f"{mode_cases[case_file.stem].title!r}），该文件被忽略"))
        return
    raw = _safe_read_json(case_file)
    if raw is None:
        library.issues.append(LoadIssue(
            str(case_file), "error",
            "案例文件无法读取（缺失/权限/编码错误）或不是合法 JSON 对象"))
        return
    if not isinstance(raw.get("profile"), dict):
        library.issues.append(LoadIssue(
            str(case_file), "error",
            "案例必须内嵌卡片档案 profile 对象（profile 与案例绑定，无共享档案库）"))
        return
    try:
        case = TestCase.model_validate(raw)
    except (ValidationError, json.JSONDecodeError) as exc:
        library.issues.append(LoadIssue(str(case_file), "error",
                                        f"invalid case: {exc}"))
        return
    if case.case_id != case_file.stem:
        library.issues.append(LoadIssue(str(case_file), "warning",
                                        f"case_id {case.case_id!r} differs "
                                        f"from file name {case_file.stem!r}"))
    if case.scheme != scheme:
        library.issues.append(LoadIssue(str(case_file), "error",
                                        f"case scheme {case.scheme!r} != "
                                        f"directory scheme {scheme!r}"))
        return
    if case.entry_mode.value != mode:
        library.issues.append(LoadIssue(str(case_file), "error",
                                        f"case entry_mode {case.entry_mode.value!r} "
                                        f"!= directory {mode!r}"))
        return

    # profile ↔ case binding: missing id -> case_id; mismatch -> warning only
    if not case.profile.id:
        case.profile.id = case.case_id
    elif case.profile.id != case.case_id:
        library.issues.append(LoadIssue(
            str(case_file), "warning",
            f"profile.id {case.profile.id!r} != case_id {case.case_id!r} "
            f"(建议保持一致，档案随案例绑定)"))

    for issue in validate_profile(case.profile).issues:
        library.issues.append(LoadIssue(
            str(case_file), issue.severity,
            f"[{case.case_id}] {issue.message}"))
    if case.keys is not None:
        for issue in validate_keys(case.keys, case.case_id).issues:
            library.issues.append(LoadIssue(str(case_file), issue.severity,
                                            f"[{case.case_id}] {issue.message}"))
    for issue in validate_pos_params(case.transaction.pos_params, case.case_id).issues:
        library.issues.append(LoadIssue(str(case_file), issue.severity,
                                        f"[{case.case_id}] {issue.message}"))
    # v1.2: a case-level set overrides the scheme default entirely (R2)
    if case.kernel_params is not None:
        for issue in validate_kernel_params(case.kernel_params, case.case_id).issues:
            library.issues.append(LoadIssue(str(case_file), issue.severity,
                                            f"[{case.case_id}] {issue.message}"))

    if group_id is not None:
        case.group_id = group_id
    mode_cases[case.case_id] = case


def _load_group_dir(group_dir: Path, scheme: str, mode: str,
                    mode_cases: dict[str, TestCase],
                    mode_groups: dict[str, GroupDef],
                    group_paths: dict[str, str],
                    library: CaseLibrary) -> None:
    """Load one 总案例 directory: group.json (description) + 子案例 JSONs."""
    group_file = group_dir / "group.json"
    if not group_file.is_file():
        library.issues.append(LoadIssue(str(group_dir), "warning",
                                        "group directory has no group.json, skipped"))
        return
    raw_group = _safe_read_json(group_file)
    if raw_group is None:
        library.issues.append(LoadIssue(
            str(group_file), "error",
            "group.json 无法读取（缺失/权限/编码错误）或不是合法 JSON 对象"))
        return
    try:
        group = GroupDef.model_validate(raw_group)
    except (ValidationError, json.JSONDecodeError) as exc:
        library.issues.append(LoadIssue(str(group_file), "error",
                                        f"invalid group.json: {exc}"))
        return
    if group.group_id != group_dir.name:
        library.issues.append(LoadIssue(str(group_file), "warning",
                                        f"group_id {group.group_id!r} differs "
                                        f"from directory name {group_dir.name!r}"))
    if group.scheme != scheme:
        library.issues.append(LoadIssue(str(group_file), "error",
                                        f"group scheme {group.scheme!r} != "
                                        f"directory scheme {scheme!r}"))
        return
    if group.entry_mode.value != mode:
        library.issues.append(LoadIssue(str(group_file), "error",
                                        f"group entry_mode {group.entry_mode.value!r} "
                                        f"!= directory {mode!r}"))
        return
    # D1: duplicate group_id (two directories claiming the same 总案例) and a
    # group_id that collides with an executable case_id are both errors; the
    # later definition is ignored.
    if group.group_id in mode_cases:
        library.issues.append(LoadIssue(
            str(group_file), "error",
            f"group_id {group.group_id!r} 与已有案例号冲突"
            f"（{mode_cases[group.group_id].title!r}）：case_id 与 group_id 互斥，"
            f"该总案例被忽略"))
        return
    if group.group_id in mode_groups:
        library.issues.append(LoadIssue(
            str(group_file), "error",
            f"group_id {group.group_id!r} 重复（"
            f"{group_paths.get(group.group_id, '')} 已定义），该目录被忽略"))
        return
    mode_groups[group.group_id] = group
    group_paths[group.group_id] = str(group_file)
    # NOTE: the group's 子案例 files are loaded by load_library() in the single
    # deterministic pass that also covers standalone cases, so duplicate-id
    # resolution has one well-defined order.  Do NOT load them here.


def _check_group_case_id_collisions(scheme: str, mode: str,
                                    mode_cases: dict[str, TestCase],
                                    mode_groups: dict[str, GroupDef],
                                    group_paths: dict[str, str],
                                    library: CaseLibrary) -> None:
    """Report every case whose id equals a 总案例 group_id (D1).

    Groups load first, so a 子案例 colliding with its own group id is already
    caught per file; this pass covers cases that arrived before/after a group
    and makes the conflict visible for every affected case.
    """
    for case_id in sorted(set(mode_cases) & set(mode_groups)):
        library.issues.append(LoadIssue(
            group_paths.get(case_id, ""), "error",
            f"case_id {case_id!r} 与总案例 group_id 冲突"
            f"（{scheme}/{mode}）：case_id 与 group_id 互斥，该案例不可执行"))


def _load_scheme_kernel_params(scheme_dir: Path, scheme: str,
                               library: CaseLibrary) -> KernelParams | None:
    """Load the gitignored local ``kernel_params.json`` for one scheme.

    Missing file -> None (the scheme pushes nothing; the POS keeps its built-in
    tables).  Unreadable/invalid file -> an error issue and None, never a crash.
    """
    path = scheme_dir / KERNEL_PARAMS_FILE
    if not path.is_file():
        return None
    raw = _safe_read_json(path)
    if raw is None:
        library.issues.append(LoadIssue(
            str(path), "error", "kernel_params.json 无法读取或不是合法 JSON 对象"))
        return None
    try:
        params = KernelParams.model_validate(raw)
    except ValidationError as exc:
        library.issues.append(LoadIssue(
            str(path), "error", f"invalid kernel_params.json: {exc}"))
        return None
    logger.info("scheme %s: loaded local kernel params (AID=%d CAPK=%d term=%s)",
                scheme, len(params.aids), len(params.caps),
                "yes" if params.term else "no")
    return params


def _safe_read_json(path: Path) -> dict[str, Any] | None:
    """Read one JSON object, converting every read/parse failure into ``None``.

    The loader must never propagate an exception for a bad case file (D1): a
    single malformed JSON used to abort the whole library scan.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        logger.warning("cannot read %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        logger.warning("%s is not a JSON object", path)
        return None
    return data
