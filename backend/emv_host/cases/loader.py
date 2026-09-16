"""Case library loader: scans the cases directory, validates JSONs, indexes.

Layout (root = ``--cases`` dir)::

    cases/
      <brand>/
        brand.json                      brand metadata + card_template + pos_params
        contactless/cases/<case_id>.json              standalone self-contained case
        contactless/cases/<group_id>/group.json       总案例 (description only, no profile)
        contactless/cases/<group_id>/<group_id>-NN.json  子案例 (self-contained case)

Every executable case is self-contained: ``profile`` (and optional ``keys``)
are embedded in the case JSON and bound to the case (v2 binding); there is no
brand-level shared profile library any more.  A 总案例 group carries no
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
    BrandDef,
    TestCase,
)
from .validation import (
    validate_kernel_params,
    validate_keys,
    validate_pos_params,
    validate_profile,
)

logger = logging.getLogger("emv_host.cases")

#: Commit-safe name for the optional per-brand kernel parameter file that may
#: sit next to ``brand.json``.  Kept only for backward compatibility: since C3
#: the file holds CAPK moduli (key material), so production deployments must
#: keep it OUTSIDE the repository and load it via ``kernel_params_dir``.
KERNEL_PARAMS_FILE = "kernel_params.json"

#: File name used inside the external (out-of-repo) kernel params directory:
#: ``<kernel_params_dir>/<brand>.json``.
EXTERNAL_KERNEL_PARAMS_SUFFIX = ".json"


class CaseLibraryError(Exception):
    """Fatal library problem (bad root dir, no brands...)."""


@dataclass
class LoadIssue:
    path: str
    severity: str  # error | warning
    message: str


@dataclass
class CaseLibrary:
    root: Path
    brands: dict[str, BrandDef] = field(default_factory=dict)
    cases: dict[tuple[str, str], dict[str, TestCase]] = field(default_factory=dict)
    # (brand, mode) -> {group_id: GroupDef}; sub-cases carry group_id
    groups: dict[tuple[str, str], dict[str, GroupDef]] = field(default_factory=dict)
    issues: list[LoadIssue] = field(default_factory=list)
    #: External, out-of-repo directory holding ``<brand>.json`` kernel parameter
    #: sets (C3).  Takes precedence over the in-repo legacy file.
    kernel_params_dir: Path | None = None
    #: When True, also accept the legacy in-repo ``<brand>/kernel_params.json``
    #: (compatibility only; it carries CAPK moduli so it is off by default).
    use_legacy_kernel_params: bool = False
    #: v1.2.x: named POS configs from <kernel_params_dir>/<brand>/<ref>.json
    named_kernel_params: dict[tuple[str, str], KernelParams] = field(
        default_factory=dict)

    # -- kernel params -------------------------------------------------------
    def kernel_params_for(self, brand: str) -> KernelParams | None:
        """The POS kernel parameter set that applies to ``brand``.

        Brand-level only; per-case overrides are applied by the runner.  One
        lookup point so the loader, the API and the executor cannot disagree.
        """
        brand_def = self.brands.get(brand)
        return brand_def.kernel_params if brand_def is not None else None

    def named_kernel_params_for(self, brand: str,
                                ref: str) -> KernelParams | None:
        """One named POS config from <kernel-params-dir>/<brand>/<ref>.json."""
        return self.named_kernel_params.get((brand, ref))

    # -- lookups ------------------------------------------------------------
    def brand_cases(self, brand: str, mode: str) -> dict[str, TestCase]:
        return self.cases.get((brand, mode), {})

    def get_case(self, brand: str, mode: str, case_id: str) -> TestCase | None:
        return self.brand_cases(brand, mode).get(case_id)

    def brand_groups(self, brand: str, mode: str) -> dict[str, GroupDef]:
        return self.groups.get((brand, mode), {})

    def get_group(self, brand: str, mode: str, group_id: str) -> GroupDef | None:
        return self.brand_groups(brand, mode).get(group_id)

    def group_child_ids(self, brand: str, mode: str, group_id: str) -> list[str]:
        return sorted(cid for cid, tc in self.brand_cases(brand, mode).items()
                      if tc.group_id == group_id)

    def entry_modes_for(self, brand: str) -> list[str]:
        brand_def = self.brands.get(brand)
        if brand_def is None:
            return []
        return [m.value for m in brand_def.entry_modes]

    def all_case_ids(self, brand: str, mode: str) -> list[str]:
        return sorted(self.brand_cases(brand, mode).keys())

    # -- tree for the web UI ------------------------------------------------
    def tree(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for brand in sorted(self.brands):
            brand_def = self.brands[brand]
            modes = []
            for mode in brand_def.entry_modes:
                mode_cases = self.brand_cases(brand, mode.value)
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
                        self._group_node(brand, mode.value, g)
                        for g in sorted(
                            self.brand_groups(brand, mode.value).values(),
                            key=lambda x: x.group_id)
                    ],
                })
            out.append({
                "brand": brand,
                "display_name": brand_def.display_name,
                "description": brand_def.description,
                "case_count": sum(m["case_count"] for m in modes),
                "entry_modes": modes,
            })
        return out

    def _group_node(self, brand: str, mode: str, group: GroupDef) -> dict[str, Any]:
        children = sorted(
            (tc for tc in self.brand_cases(brand, mode).values()
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

    def public_case(self, brand: str, mode: str, case_id: str) -> dict[str, Any] | None:
        tc = self.get_case(brand, mode, case_id)
        if tc is None:
            return None
        profile = tc.profile
        return {
            "case_id": tc.case_id,
            "title": tc.title,
            "summary": tc.summary,
            "brand": tc.brand,
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
            # v1.2: per-case kernel parameter override (None = brand default)
            "kernel_params": (None if tc.kernel_params is None
                              else tc.kernel_params.model_dump(mode="json")),
            "kernel_params_ref": tc.kernel_params_ref,
            "terminal_profile_ref": tc.terminal_profile_ref,
        }

    def reload(self) -> None:
        """Re-scan the cases directory in place so new files added at runtime
        become visible without restarting the host.  Existing references to
        this instance (e.g. in the runner) keep working."""
        fresh = load_library(self.root,
                             kernel_params_dir=self.kernel_params_dir,
                             use_legacy_kernel_params=self.use_legacy_kernel_params)
        self.brands = fresh.brands
        self.cases = fresh.cases
        self.groups = fresh.groups
        self.issues = fresh.issues
        self.kernel_params_dir = fresh.kernel_params_dir
        self.use_legacy_kernel_params = fresh.use_legacy_kernel_params
        self.named_kernel_params = fresh.named_kernel_params


def load_library(root: str | Path,
                 kernel_params_dir: str | Path | None = None,
                 use_legacy_kernel_params: bool = False) -> CaseLibrary:
    """Scan ``root`` for brands/cases.

    ``kernel_params_dir`` (C3) is an out-of-repo directory holding one
    ``<brand>.json`` per brand — the supported location, because those files
    carry CAPK moduli.  ``use_legacy_kernel_params`` additionally accepts the
    old in-repo ``<brand>/kernel_params.json``; it defaults to False so key
    material is never picked up from inside the repository unless asked for.
    """
    root = Path(root)
    external = Path(kernel_params_dir) if kernel_params_dir else None
    library = CaseLibrary(root=root, kernel_params_dir=external,
                          use_legacy_kernel_params=use_legacy_kernel_params)
    if not root.is_dir():
        raise CaseLibraryError(f"cases root does not exist: {root}")
    if external is not None and not _external_has_any_config(external):
        library.issues.append(LoadIssue(
            str(external), "error",
            "kernel params dir 已配置但目录内没有任何配置"
            "（<brand>.json / <brand>/<ref>.json 均不存在）"))

    for brand_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        brand_json = brand_dir / "brand.json"
        if not brand_json.is_file():
            library.issues.append(LoadIssue(str(brand_json), "warning",
                                            "directory has no brand.json, skipped"))
            continue
        raw_brand = _safe_read_json(brand_json)
        if raw_brand is None:
            library.issues.append(LoadIssue(str(brand_json), "error",
                                            "brand.json 无法读取或不是合法 JSON 对象"))
            continue
        try:
            brand_def = BrandDef.model_validate(raw_brand)
        except (ValidationError, json.JSONDecodeError) as exc:
            library.issues.append(LoadIssue(str(brand_json), "error",
                                            f"invalid brand.json: {exc}"))
            continue
        brand = brand_def.brand
        # D1: two directories declaring the same brand key would silently shadow
        # each other (the second wins in library.brands) — reject the later one.
        if brand in library.brands:
            library.issues.append(LoadIssue(
                str(brand_json), "error",
                f"brand {brand!r} 重复定义（同一 cases 根目录下另一个目录已声明），"
                f"该目录被忽略"))
            continue
        library.brands[brand] = brand_def

        # C3: brand-level kernel parameters.  The external directory (out of
        # repo) wins; the legacy in-repo file is a compatibility fallback.  The
        # data is never in the committed brand.json because it carries CAPK
        # moduli (key material).
        params, params_path = _load_brand_kernel_params(
            brand_dir, brand, library)
        brand_def.kernel_params = params

        # brand-level defaults are advisory (clone templates), not shared
        # runnable profiles; validate them but never block the brand itself.
        if brand_def.card_template is not None:
            for issue in validate_profile(brand_def.card_template).issues:
                library.issues.append(LoadIssue(
                    str(brand_json), issue.severity,
                    f"[card_template:{issue.profile_id or brand}] {issue.message}"))
        for issue in validate_pos_params(brand_def.pos_params, brand).issues:
            library.issues.append(LoadIssue(str(brand_json), issue.severity,
                                            f"[pos_params] {issue.message}"))
        if brand_def.kernel_params is not None:
            for issue in validate_kernel_params(
                    brand_def.kernel_params, f"{brand}:kernel_params").issues:
                library.issues.append(LoadIssue(
                    str(params_path), issue.severity,
                    f"[{brand}] {issue.message}"))

        # v1.2.x named POS config library (out-of-repo, per brand dir)
        _load_named_kernel_params(brand, library)

        # cases under each entry-mode dir: flat *.json (standalone) plus
        # sub-directories holding a group (group.json + 子案例 *.json files)
        for mode in brand_def.entry_modes:
            mode_dir = brand_dir / mode.value / "cases"
            if not mode_dir.is_dir():
                library.issues.append(LoadIssue(str(mode_dir), "warning",
                                                f"no cases dir for {brand}/{mode.value}"))
                continue
            mode_cases: dict[str, TestCase] = {}
            mode_groups: dict[str, GroupDef] = {}
            group_paths: dict[str, str] = {}
            # Groups first: every case must be checked against the full set of
            # group_ids in this (brand, mode) regardless of directory order.
            for item in sorted(mode_dir.iterdir()):
                if item.is_dir():
                    _load_group_dir(item, brand, mode.value, mode_cases,
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
                _load_case_file(case_file, brand, mode.value, mode_cases,
                                mode_groups, group_paths, library,
                                group_id=owner_group)
            _check_group_case_id_collisions(brand, mode.value, mode_cases,
                                            mode_groups, group_paths, library)
            library.cases[(brand, mode.value)] = mode_cases
            library.groups[(brand, mode.value)] = mode_groups

    if not library.brands:
        raise CaseLibraryError(f"no valid brands found under {root}")
    return library


def _load_case_file(case_file: Path, brand: str, mode: str,
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
            f"case_id {case_file.stem!r} 重复（{brand}/{mode} 中已存在同号案例："
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
    if case.brand != brand:
        library.issues.append(LoadIssue(str(case_file), "error",
                                        f"case brand {case.brand!r} != "
                                        f"directory brand {brand!r}"))
        return
    if case.entry_mode.value != mode:
        library.issues.append(LoadIssue(str(case_file), "error",
                                        f"case entry_mode {case.entry_mode.value!r} "
                                        f"!= directory {mode!r}"))
        return

    # v1.2.x: resolve modular named POS configs (CAPK moduli stay out of the
    # case file; the composed object is in-memory only).  CONF and Profile are
    # paired freely by the case.
    refs = [("kernel_params_ref", case.kernel_params_ref),
            ("terminal_profile_ref", case.terminal_profile_ref)]
    active_refs = [(name, ref) for name, ref in refs if ref]
    if case.kernel_params is not None and active_refs:
        library.issues.append(LoadIssue(
            str(case_file), "error",
            f"[{case.case_id}] kernel_params 与 kernel_params_ref/"
            f"terminal_profile_ref 不能同时设置"))
    elif active_refs:
        resolved: dict[str, KernelParams] = {}
        for field_name, ref in active_refs:
            params = library.named_kernel_params_for(brand, ref)
            if params is None:
                library.issues.append(LoadIssue(
                    str(case_file), "error",
                    f"[{case.case_id}] {field_name} {ref!r} 不存在于 {brand} "
                    f"的命名 POS 配置库 "
                    f"(<kernel-params-dir>/{brand}/{ref}.json)"))
            else:
                resolved[field_name] = params
        if len(resolved) == len(active_refs):
            case.kernel_params = _compose_kernel_params(
                resolved.get("kernel_params_ref"),
                resolved.get("terminal_profile_ref"))

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
    # v1.2: a case-level set overrides the brand default entirely (R2)
    if (case.kernel_params is not None and not case.kernel_params_ref
            and not case.terminal_profile_ref):
        for issue in validate_kernel_params(case.kernel_params, case.case_id).issues:
            library.issues.append(LoadIssue(str(case_file), issue.severity,
                                            f"[{case.case_id}] {issue.message}"))

    if group_id is not None:
        case.group_id = group_id
    mode_cases[case.case_id] = case


def _load_group_dir(group_dir: Path, brand: str, mode: str,
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
    if group.brand != brand:
        library.issues.append(LoadIssue(str(group_file), "error",
                                        f"group brand {group.brand!r} != "
                                        f"directory brand {brand!r}"))
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


def _check_group_case_id_collisions(brand: str, mode: str,
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
            f"（{brand}/{mode}）：case_id 与 group_id 互斥，该案例不可执行"))


def _external_has_any_config(external: Path) -> bool:
    """True when the kernel-params dir holds at least one config file.

    Either a top-level ``<brand>.json`` or a named-library
    ``<brand>/<ref>.json``.  Used to tell a misconfigured (empty) directory
    from a directory that legitimately covers only some brands.
    """
    if not external.is_dir():
        return False
    for entry in external.iterdir():
        if entry.is_file() and entry.suffix == ".json":
            return True
        if entry.is_dir() and any(entry.glob("*.json")):
            return True
    return False


def _load_brand_kernel_params(brand_dir: Path, brand: str,
                               library: CaseLibrary
                               ) -> tuple[KernelParams | None, Path | None]:
    """Load one brand's POS kernel parameter set (C3).

    Precedence:

    1. ``<kernel_params_dir>/<brand>.json`` — the external, out-of-repo
       location (the supported production form; it holds CAPK moduli);
    2. ``<brand_dir>/kernel_params.json`` — legacy in-repo fallback.

    Returns ``(params, path)``.  A missing file yields ``(None, None)``: the
    brand then pushes nothing and the POS keeps its built-in tables.  An
    unreadable/invalid file yields an error issue and ``(None, path)`` — never a
    crash.

    The two locations are NOT merged, and the legacy in-repo file is read only
    when explicitly opted into (``use_legacy_kernel_params``).  Default-off is
    deliberate (C3): the file carries CAPK moduli, so a misconfigured path must
    surface as an error rather than silently falling back to key material that
    is supposed to have left the workspace.  A directory that covers only some
    brands is not an error; only a completely empty/nonexistent dir is
    reported, once, by ``load_library`` (v1.2.x, option c).
    """
    external = library.kernel_params_dir
    if external is not None:
        path = external / f"{brand}{EXTERNAL_KERNEL_PARAMS_SUFFIX}"
        if not path.is_file():
            # (c) Per-brand absence is normal: the dir may hold configs for
            # only some brands and/or a named library <dir>/<brand>/.  A
            # completely empty dir is reported once in load_library().
            return None, None
    elif library.use_legacy_kernel_params:
        path = brand_dir / KERNEL_PARAMS_FILE
        if not path.is_file():
            return None, None
    else:
        return None, None
    raw = _safe_read_json(path)
    if raw is None:
        library.issues.append(LoadIssue(
            str(path), "error", f"{path.name} 无法读取或不是合法 JSON 对象"))
        return None, path
    try:
        params = KernelParams.model_validate(raw)
    except ValidationError as exc:
        library.issues.append(LoadIssue(
            str(path), "error", f"invalid {path.name}: {exc}"))
        return None, path
    logger.info("brand %s: loaded kernel params from %s (AID=%d CAPK=%d term=%s)",
                brand, path, len(params.aids), len(params.caps),
                "yes" if params.term else "no")
    return params, path


def _compose_kernel_params(conf: KernelParams | None,
                           profile: KernelParams | None) -> KernelParams | None:
    """Freely pair a CONF module with a Profile module (v1.2.x).

    ``term`` = CONF TerminalParameter TLVs followed by the Profile tags;
    AID/CAPK/DRL/ExceptionFile come from the CONF module only.
    """
    if conf is None and profile is None:
        return None
    if conf is None:
        return profile
    if profile is None:
        return conf
    term = (conf.term + profile.term) if (conf.term and profile.term) \
        else (conf.term or profile.term)
    return KernelParams(
        aids=conf.aids,
        caps=conf.caps,
        drl=conf.drl,
        exception_file=conf.exception_file,
        term=term,
        version=conf.version or profile.version,
    )


def _load_named_kernel_params(brand: str, library: CaseLibrary) -> None:
    """Load one brand's named POS config library (v1.2.x).

    Layout: <kernel_params_dir>/<brand>/<ref>.json -- each file is a
    KernelParams document; index.json (metadata manifest) is ignored.
    Named configs are vendor test vectors, so their CAPK DF03 is trusted
    verbatim (trust_checksum=True: a mismatch warns instead of blocking).
    """
    external = library.kernel_params_dir
    if external is None:
        return
    brand_dir = external / brand
    if not brand_dir.is_dir():
        return
    for path in sorted(brand_dir.glob("*.json")):
        if path.name == "index.json":
            continue
        raw = _safe_read_json(path)
        if raw is None:
            library.issues.append(LoadIssue(
                str(path), "error", f"{path.name} 无法读取或不是合法 JSON 对象"))
            continue
        try:
            params = KernelParams.model_validate(raw)
        except ValidationError as exc:
            library.issues.append(LoadIssue(
                str(path), "error", f"invalid {path.name}: {exc}"))
            continue
        ref = path.stem
        library.named_kernel_params[(brand, ref)] = params
        for issue in validate_kernel_params(
                params, f"{brand}:{ref}", trust_checksum=True).issues:
            library.issues.append(LoadIssue(str(path), issue.severity,
                                            f"[{brand}:{ref}] {issue.message}"))
        logger.info("brand %s: loaded named POS config %s from %s "
                    "(AID=%d CAPK=%d DRL=%d Exception=%d term=%s)",
                    brand, ref, path, len(params.aids), len(params.caps),
                    len(params.drl), len(params.exception_file),
                    "yes" if params.term else "no")


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
