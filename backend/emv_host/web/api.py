"""FastAPI app: REST control surface, WebSocket event stream, static hosting.

REST responses are authoritative; WS events (monotonic ``seq``) only refresh.
On WS connect the server sends a full snapshot first, then replays buffered
events newer than ``?since=``, then live events.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from ..cases import CaseLibrary, EntryMode, GroupDef, TestCase
from ..cases.loader import CaseLibraryError
from ..cases.validation import (
    validate_kernel_params,
    validate_keys,
    validate_pos_params,
    validate_profile,
)
from ..runner import RunExecutor
from ..store.run_store import RunStore
from .events import EventBus

logger = logging.getLogger("emv_host.web")

PASSING_STATUSES = {"PASS", "WARN"}


@dataclass
class AppContext:
    library: CaseLibrary
    executor: RunExecutor
    store: RunStore
    bus: EventBus
    static_dir: str


class RunRequest(BaseModel):
    scheme: str
    mode: str
    kind: str = Field(default="full")  # full | selected | single | resume | group
    case_ids: list[str] = Field(default_factory=list)
    group_id: str | None = None        # v1.2: "运行本组" (kind=group)
    resume_from_run: str | None = None
    # Q9: a FAIL always stops the run; the field is accepted for compatibility
    # but "continue" is ignored by the executor.
    on_failure: str | None = Field(default=None, pattern="^(stop|continue)$")
    retry_count: int | None = Field(default=None, ge=0, le=5)


def _snapshot(ctx: AppContext) -> dict[str, Any]:
    return {
        "devices": ctx.executor.snapshot()["devices"],
        "session": ctx.executor.snapshot()["session"],
    }


def _resolve_inside(root: Path, *parts: str, label: str) -> Path:
    """Resolve ``root/parts...`` and refuse to escape the case library root.

    A crafted ``case_id``/``group_id`` (``../``, an absolute path, a symlinked
    directory) used to let the add-case/add-group endpoints create or overwrite
    files anywhere the host could write.  Every id is already pattern-checked by
    its model, so this is the second, path-level line of defence.
    """
    target = root.joinpath(*parts).resolve()
    resolved_root = root.resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise HTTPException(400, f"{label} 解析后落在用例库之外：{target}")
    return target


def create_app(ctx: AppContext) -> FastAPI:
    app = FastAPI(title="EMV Test Host", docs_url="/api/docs",
                  openapi_url="/api/openapi.json")

    # -- request logging ------------------------------------------------------
    # uvicorn.access goes to its own console handler and is not written to
    # host.log, so mirror every /api HTTP call onto the emv_host.web logger:
    # a 409 (or any control endpoint status) can then be correlated in the
    # file log with the executor state diagnostics around it.
    @app.middleware("http")
    async def log_api_requests(request: Request, call_next: Any) -> Any:
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("HTTP %s %s raised", request.method, path)
            raise
        logger.info("HTTP %s %s -> %s (%.0f ms)", request.method, path,
                    response.status_code, (time.monotonic() - start) * 1000)
        return response

    # -- health / status -----------------------------------------------------
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        snap = ctx.executor.snapshot()
        return {
            "status": "ok",
            "busy": ctx.executor.busy,
            "devices": snap["devices"],
            "session": snap["session"],
        }

    @app.get("/api/run")
    async def current_run() -> dict[str, Any]:
        return _snapshot(ctx)

    # -- case library ----------------------------------------------------------
    @app.post("/api/library/reload")
    async def reload_library() -> dict[str, Any]:
        """Re-read the case library from disk (联调时手工改用例后免重启）。"""
        try:
            ctx.library.reload()
        except CaseLibraryError as exc:
            raise HTTPException(400, f"用例库重载失败: {exc}") from exc
        errors = [i for i in ctx.library.issues if i.severity == "error"]
        counts = {f"{s['scheme']}/{m['mode']}": len(m["cases"])
                  for s in ctx.library.tree() for m in s["entry_modes"]}
        logger.info("case library reloaded: %d scheme/mode groups, %d errors",
                    len(counts), len(errors))
        await ctx.bus.publish("library", {"event": "reloaded",
                                          "counts": counts})
        return {"ok": not errors, "counts": counts, "total": sum(counts.values()),
                "errors": [i.message for i in errors]}

    @app.get("/api/tree")
    async def tree() -> dict[str, Any]:
        return {"schemes": ctx.library.tree(),
                "issues": [i.__dict__ for i in ctx.library.issues]}

    @app.get("/api/cases/{scheme}/{mode}")
    async def list_cases(scheme: str, mode: str) -> dict[str, Any]:
        cases = ctx.library.scheme_cases(scheme, mode)
        if not cases:
            raise HTTPException(404, f"no cases for {scheme}/{mode}")
        return {
            "cases": [
                {"case_id": tc.case_id, "title": tc.title,
                 "summary": tc.summary, "entry_mode": tc.entry_mode.value}
                for tc in sorted(cases.values(), key=lambda c: c.case_id)
            ],
        }

    @app.get("/api/cases/{scheme}/{mode}/{case_id}")
    async def case_detail(scheme: str, mode: str, case_id: str) -> dict[str, Any]:
        data = ctx.library.public_case(scheme, mode, case_id)
        if data is None:
            raise HTTPException(404, f"case {scheme}/{mode}/{case_id} not found")
        return data

    # -- case creation (web "add case" form / JSON import) ---------------------
    @app.get("/api/scheme/{scheme}/template")
    async def scheme_template(scheme: str) -> dict[str, Any]:
        """Scheme-level defaults for the add-case page: the default card
        template (cloned into a new case's embedded profile) and the scheme's
        default POS transaction parameter template (auto-added per case)."""
        scheme_def = ctx.library.schemes.get(scheme)
        if scheme_def is None:
            raise HTTPException(404, f"scheme {scheme!r} not found")
        return {
            "card_template": None if scheme_def.card_template is None
            else scheme_def.card_template.model_dump(mode="json"),
            "pos_params": [p.model_dump() for p in scheme_def.pos_params],
        }

    def _validate_case_payload(payload: Any) -> tuple[TestCase | None, list[str]]:
        """Validate a raw case JSON.  Returns (case, errors); case is None when
        there are errors.  Every message is human-readable for the web UI.

        ``group_id`` (optional): when present the case is a 子案例 of that
        总案例 — the loader binds it from the group directory.  An executable
        case whose id collides with a 总案例 group id is rejected."""
        errors: list[str] = []
        if not isinstance(payload, dict):
            return None, ["请求体必须是 JSON 对象"]
        try:
            case = TestCase.model_validate(payload)
        except ValidationError as exc:
            for err in exc.errors():
                loc = ".".join(str(p) for p in err["loc"]) or "(root)"
                errors.append(f"{loc}: {err['msg']}")
            return None, errors

        if case.scheme not in ctx.library.schemes:
            errors.append(f"卡组 {case.scheme!r} 不存在")
        if case.entry_mode != EntryMode.CONTACTLESS:
            errors.append("本后台仅支持 contactless（非接）测试")
        # profile is embedded & bound to the case (v2); a missing/invalid
        # profile blocks creation
        if not isinstance(payload.get("profile"), dict):
            errors.append("案例必须内嵌卡片档案 profile 对象（本版本档案与案例绑定，"
                          "无卡组共享档案库）")
        if not case.profile.id:
            case.profile.id = case.case_id  # binding: auto-fill with case_id
        mode = case.entry_mode.value
        # 子案例 target group (optional; directory binding is authoritative)
        group_id = (payload.get("group_id") or "").strip() or None
        if group_id:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", group_id):
                errors.append(f"group_id 只能包含字母/数字/下划线/连字符：{group_id!r}")
            elif ctx.library.get_group(case.scheme, mode, group_id) is None:
                errors.append(f"所属总案例 {group_id!r} 不存在于卡组 {case.scheme!r} "
                              f"（请先创建总案例，或去掉选择作为独立案例）")
        if ctx.library.get_case(case.scheme, mode, case.case_id) is not None:
            errors.append(f"案例号 {case.case_id!r} 已存在")
        if ctx.library.get_group(case.scheme, mode, case.case_id) is not None:
            errors.append(f"案例号 {case.case_id!r} 与总案例 group_id 冲突"
                          f"（总案例不直接执行，请另选子案例号）")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", case.case_id):
            errors.append("案例号只能包含字母、数字、下划线、连字符")
        if not case.expected_results:
            errors.append("expected_results 至少需要 1 条断言规则")

        # deep validation of the embedded payload with Chinese messages
        for issue in validate_profile(case.profile).issues:
            if issue.severity == "error":
                errors.append(f"profile: {issue.message}")
        if case.keys is not None:
            for issue in validate_keys(case.keys, case.case_id).issues:
                if issue.severity == "error":
                    errors.append(f"keys: {issue.message}")
        for issue in validate_pos_params(
                case.transaction.pos_params, case.case_id).issues:
            if issue.severity == "error":
                errors.append(f"pos_params: {issue.message}")
        # v1.2: case-level POS kernel parameters (AID/CAPK/TERM)
        for issue in validate_kernel_params(case.kernel_params, case.case_id).issues:
            if issue.severity == "error":
                errors.append(f"kernel_params: {issue.message}")
        return (case, errors)

    @app.post("/api/cases/validate")
    async def validate_case(payload: Any = Body(...)) -> dict[str, Any]:
        _, errors = _validate_case_payload(payload)
        return {"ok": not errors, "errors": errors}

    @app.post("/api/cases")
    async def create_case(payload: Any = Body(...)) -> dict[str, Any]:
        case, errors = _validate_case_payload(payload)
        if errors:
            raise HTTPException(400, detail=errors)

        mode = case.entry_mode.value  # always "contactless" here
        group_id = (payload.get("group_id") or "").strip() or None
        # v1.2: resolve and confirm the target stays inside the case library
        # before creating anything
        parts = [case.scheme, mode, "cases"] + ([group_id] if group_id else []) \
            + [f"{case.case_id}.json"]
        path = _resolve_inside(ctx.library.root, *parts, label="案例路径")
        case_dir = path.parent
        try:
            case_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(400, f"无法创建用例目录: {exc}") from exc
        if path.exists():
            raise HTTPException(409, f"案例号 {case.case_id!r} 已存在")
        body_dict = case.model_dump(mode="json")
        body_dict.pop("group_id", None)  # binding comes from the directory
        body = json.dumps(body_dict, ensure_ascii=False, indent=2) + "\n"
        path.write_text(body, encoding="utf-8")

        # hot-reload the library; roll the file back if the loader rejects it
        try:
            ctx.library.reload()
        except CaseLibraryError as exc:
            path.unlink(missing_ok=True)
            raise HTTPException(400, f"用例未通过库校验: {exc}") from exc
        new_issues = [i for i in ctx.library.issues
                      if i.severity == "error" and str(path) in i.path]
        if new_issues:
            path.unlink(missing_ok=True)
            ctx.library.reload()
            raise HTTPException(400, detail=[i.message for i in new_issues])

        await ctx.bus.publish("library", {"event": "case_added",
                                          "case_id": case.case_id,
                                          "group_id": group_id,
                                          "scheme": case.scheme})
        logger.info("case added via web UI: %s (%s%s)", case.case_id,
                    f"{group_id}/" if group_id else "", path)
        return {"ok": True, "case_id": case.case_id,
                "group_id": group_id, "path": str(path)}

    # -- group (总案例) creation ----------------------------------------------
    def _validate_group_payload(payload: Any) -> tuple[GroupDef | None, list[str]]:
        """Validate a 总案例 JSON (description + test plan only — no profile,
        no transaction params, no expected results)."""
        errors: list[str] = []
        if not isinstance(payload, dict):
            return None, ["请求体必须是 JSON 对象"]
        try:
            group = GroupDef.model_validate(payload)
        except ValidationError as exc:
            for err in exc.errors():
                loc = ".".join(str(p) for p in err["loc"]) or "(root)"
                errors.append(f"{loc}: {err['msg']}")
            return None, errors
        if group.scheme not in ctx.library.schemes:
            errors.append(f"卡组 {group.scheme!r} 不存在")
        if group.entry_mode != EntryMode.CONTACTLESS:
            errors.append("本后台仅支持 contactless（非接）测试")
        mode = group.entry_mode.value
        if not group.test_plan:
            errors.append("总案例 test_plan 至少需要 1 个步骤（总案例仅配置说明与 test plan，"
                          "不配置档案/交易参数/预期结果）")
        if ctx.library.get_group(group.scheme, mode, group.group_id) is not None:
            errors.append(f"总案例 {group.group_id!r} 已存在")
        if ctx.library.get_case(group.scheme, mode, group.group_id) is not None:
            errors.append(f"总案例号 {group.group_id!r} 与已有案例号冲突")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", group.group_id):
            errors.append("总案例号只能包含字母、数字、下划线、连字符")
        # v1.2: groups carry no kernel params of their own — a 总案例 is a
        # description only, and its 子案例 each override the scheme default.
        # Any kernel_params in a group payload is rejected, well-formed or not.
        if payload.get("kernel_params") is not None:
            errors.append("总案例不承载 kernel_params（仅子案例可逐案例配置）")
        return (group, errors)

    @app.post("/api/groups/validate")
    async def validate_group(payload: Any = Body(...)) -> dict[str, Any]:
        _, errors = _validate_group_payload(payload)
        return {"ok": not errors, "errors": errors}

    @app.post("/api/groups")
    async def create_group(payload: Any = Body(...)) -> dict[str, Any]:
        group, errors = _validate_group_payload(payload)
        if errors:
            raise HTTPException(400, detail=errors)

        mode = group.entry_mode.value  # always "contactless"
        # v1.2: resolve and confirm the target stays inside the case library
        # before creating the group directory
        path = _resolve_inside(
            ctx.library.root, group.scheme, mode, "cases", group.group_id,
            "group.json", label="总案例路径")
        group_dir = path.parent
        try:
            group_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(400, f"无法创建总案例目录: {exc}") from exc
        if path.exists():
            raise HTTPException(409, f"总案例 {group.group_id!r} 已存在")
        body = json.dumps(group.model_dump(mode="json"), ensure_ascii=False,
                          indent=2) + "\n"
        path.write_text(body, encoding="utf-8")

        try:
            ctx.library.reload()
        except CaseLibraryError as exc:
            path.unlink(missing_ok=True)
            raise HTTPException(400, f"总案例未通过库校验: {exc}") from exc
        new_issues = [i for i in ctx.library.issues
                      if i.severity == "error" and str(path) in i.path]
        if new_issues:
            path.unlink(missing_ok=True)
            ctx.library.reload()
            raise HTTPException(400, detail=[i.message for i in new_issues])

        await ctx.bus.publish("library", {"event": "group_added",
                                          "group_id": group.group_id,
                                          "scheme": group.scheme})
        logger.info("group added via web UI: %s (%s)", group.group_id, path)
        return {"ok": True, "group_id": group.group_id, "path": str(path)}

    # -- run control ------------------------------------------------------------
    @app.post("/api/run")
    async def start_run(req: RunRequest) -> dict[str, Any]:
        case_ids = list(req.case_ids)
        if req.kind in ("full", "resume"):
            all_ids = ctx.library.all_case_ids(req.scheme, req.mode)
            if req.kind == "full":
                case_ids = all_ids
            else:
                case_ids = _resume_cases(ctx, req.scheme, req.mode,
                                        req.resume_from_run)
        elif req.kind == "group":
            # v1.2 T17: "运行本组" runs exactly one 总案例's 子案例
            group_id = (req.group_id or "").strip()
            if not group_id:
                raise HTTPException(400, "kind=group requires group_id")
            if ctx.library.get_group(req.scheme, req.mode, group_id) is None:
                raise HTTPException(404, f"总案例 {group_id!r} 不存在")
            case_ids = ctx.library.group_child_ids(req.scheme, req.mode, group_id)
            if not case_ids:
                raise HTTPException(400, f"总案例 {group_id!r} 没有任何子案例")
        elif req.kind == "single":
            if len(case_ids) != 1:
                raise HTTPException(400, "kind=single requires exactly one case_id")
        elif req.kind == "selected":
            if not case_ids:
                raise HTTPException(400, "kind=selected requires case_ids")
        else:
            raise HTTPException(400, f"unknown kind {req.kind!r}")
        if not case_ids:
            raise HTTPException(400, "nothing to run")
        # 总案例 (groups) are not executable: they only group 子案例
        groups = ctx.library.scheme_groups(req.scheme, req.mode)
        if groups:
            group_hits = [gid for gid in case_ids if gid in groups]
            if group_hits:
                raise HTTPException(
                    400, f"{group_hits} 是总案例（分组），不可直接运行，"
                         f"请选择其子案例（如 {group_hits[0]}-01）或使用运行本组")
        ok, message = await ctx.executor.start(
            req.scheme, req.mode, case_ids, req.kind, req.resume_from_run,
            overrides={"on_failure": req.on_failure,
                       "retry_count": req.retry_count})
        if not ok:
            raise HTTPException(409, message)
        return {"ok": True, "run_id": message}

    @app.post("/api/run/stop")
    async def stop_run() -> dict[str, Any]:
        ok, message = await ctx.executor.stop()
        if not ok:
            raise HTTPException(409, message)
        return {"ok": True, "message": message}

    @app.post("/api/run/abort")
    async def abort_run() -> dict[str, Any]:
        ok, message = await ctx.executor.abort()
        if not ok:
            raise HTTPException(409, message)
        return {"ok": True, "message": message}

    @app.post("/api/run/skip")
    async def skip_case() -> dict[str, Any]:
        ok, message = await ctx.executor.skip()
        if not ok:
            raise HTTPException(409, message)
        return {"ok": True, "message": message}

    # -- history ----------------------------------------------------------------
    @app.get("/api/history")
    async def history(limit: int = Query(default=200, ge=1, le=1000)) -> dict[str, Any]:
        return {"runs": ctx.store.history(limit=limit)}

    @app.get("/api/history/{run_id}")
    async def history_detail(run_id: str) -> dict[str, Any]:
        run = ctx.store.get_run(run_id)
        if run is None:
            raise HTTPException(404, f"run {run_id} not found")
        results: dict[str, Any] = {}
        for case_id in run.get("queue", []):
            record = ctx.store.get_case_result(run_id, case_id)
            if record is None:
                summary = run.get("results", {}).get(case_id)
                if summary:
                    record = {"case_id": case_id, "status": summary.get("status"),
                              "attempts": summary.get("attempts", 0),
                              "note": summary.get("note", "")}
            if record is not None:
                results[case_id] = record
        quarantine: list[dict[str, Any]] = []
        try:
            qpath = ctx.store.run_dir(ctx.store.root, run_id) / "quarantine.jsonl"
            if qpath.is_file():
                for line in qpath.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        quarantine.append(json.loads(line))
        except FileNotFoundError:
            pass
        # APDU diagnostic trace (Phase A) per case, capped for the UI
        traces: dict[str, Any] = {}
        for case_id in run.get("queue", []):
            lines = ctx.store.read_trace(run_id, case_id)
            if lines:
                traces[case_id] = lines[-500:]
        return {"run": run, "results": results, "quarantine": quarantine,
                "trace": traces}

    # -- websocket ---------------------------------------------------------------
    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        queue = await ctx.bus.subscribe()
        try:
            # v1.2: a malformed ?since= must not kill the socket before the
            # snapshot is sent (it used to raise ValueError inside the handler)
            try:
                since = int(ws.query_params.get("since", "0") or "0")
            except (TypeError, ValueError):
                since = 0
            await ws.send_json({"seq": since, "kind": "snapshot",
                                "snapshot": _snapshot(ctx)})
            for message in await ctx.bus.events_since(since):
                await ws.send_json(message)
            while True:
                message = await queue.get()
                if message is None:  # bus shutdown sentinel
                    break
                await ws.send_json(message)
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass  # client gone or server shutting down
        finally:
            await ctx.bus.unsubscribe(queue)

    # -- static hosting (production) -------------------------------------------
    static = Path(ctx.static_dir)
    if static.is_dir() and (static / "index.html").is_file():
        assets = static / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(static / "index.html")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> Any:
            if full_path.startswith("api/") or full_path == "ws":
                raise HTTPException(404)
            candidate = (static / full_path).resolve()
            if full_path and candidate.is_file() and \
                    static.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(static / "index.html")
    else:
        logger.warning("static dir %s not built; run the frontend dev server "
                       "or `pnpm build`", static)

    return app


def _resume_cases(ctx: AppContext, scheme: str, mode: str,
                  resume_from_run: str | None) -> list[str]:
    """Resume: rerun every case whose last recorded status is not PASS/WARN."""
    source_run: dict[str, Any] | None = None
    if resume_from_run:
        source_run = ctx.store.get_run(resume_from_run)
    else:
        candidates = [r for r in ctx.store.history(limit=500)
                      if r["scheme"] == scheme and r["mode"] == mode]
        source_run = ctx.store.get_run(candidates[0]["run_id"]) if candidates else None
    if source_run is None:
        raise HTTPException(404, "no previous run to resume from")

    queue: list[str] = list(source_run.get("queue", []))
    results: dict[str, Any] = source_run.get("results", {})
    pending = [cid for cid in queue
               if results.get(cid, {}).get("status") not in PASSING_STATUSES]
    if not pending:
        raise HTTPException(400, "nothing to resume: every case already passed")
    return pending
