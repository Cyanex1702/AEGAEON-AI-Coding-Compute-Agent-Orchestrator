from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .bundles import (
    export_task,
    import_result,
    initialize,
    pack_result,
    restore_checkpoint,
    unpack_task,
)
from .checks import load_checks
from .provider import ModelProvider
from .session import Session


def provider_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--model", default="local")
    parser.add_argument("--context", type=int, default=8192)
    parser.add_argument("--output-tokens", type=int, default=2048)
    parser.add_argument("--permit-remote", action="store_true")
    parser.add_argument(
        "--allow-checks",
        action="store_true",
        help="execute declared commands; the workspace is not an OS sandbox",
    )


def task_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--request", required=True)
    parser.add_argument("--checks", type=Path)
    parser.add_argument("--scope", action="append", help="permitted glob; repeat for more scopes")
    parser.add_argument("--max-actions", type=int, default=12)
    parser.add_argument("--max-files", type=int, default=3)
    parser.add_argument("--allow-test-edits", action="store_true")


def model(args) -> ModelProvider:
    return ModelProvider(
        base_url=args.endpoint,
        model=args.model,
        context_tokens=args.context,
        output_tokens=args.output_tokens,
        permit_remote=args.permit_remote,
        api_key=os.getenv("AEGAEON_MODEL_KEY", ""),
    )


def task(args, mode: str) -> dict:
    return {
        "request": args.request,
        "mode": mode,
        "checks": load_checks(args.checks),
        "allowed_paths": args.scope or ["**"],
        "max_actions": args.max_actions,
        "max_changed_files": args.max_files,
        "allow_test_edits": args.allow_test_edits,
    }


def event(value: dict) -> None:
    kind = value["type"]
    if kind == "tool":
        observation = value["observation"]
        detail = observation.get("error") or observation.get("status") or value.get("path") or ""
        print(f"[{kind}] {value['action']} {detail}", flush=True)
    elif kind == "verification":
        print(f"[check] {value['observation']['id']}: {value['observation']['status']}", flush=True)
    else:
        print(f"[{kind}] {value.get('status') or value.get('message', '')}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Small-project, notebook-first coding assistant")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="read-only prerequisite report; no model requests")
    doctor.add_argument("--output", type=Path, help="optionally save the report as JSON")
    init = commands.add_parser("init", help="create an initial source Git checkpoint")
    init.add_argument("project", type=Path)
    serve = commands.add_parser("serve", help="open the lightweight local workspace app")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--data-dir", type=Path, default=Path(".aegaeon-app"))
    serve.add_argument("--open-browser", action="store_true", help="open the UI after startup")
    serve.add_argument(
        "--demo", action="store_true", help="include a labelled offline demo project"
    )
    validation = commands.add_parser(
        "validate-model", help="probe action JSON; optionally run a tiny coding benchmark"
    )
    provider_args(validation)
    validation.add_argument("--output", type=Path, required=True, help="save validation JSON")
    validation.add_argument(
        "--benchmark-dir",
        type=Path,
        help="new folder: explicitly execute generated fixture code and fixed tests",
    )
    for mode in ("ask", "fix", "test"):
        command = commands.add_parser(mode)
        command.add_argument("project", type=Path)
        task_args(command)
        provider_args(command)
    export = commands.add_parser("export-task")
    export.add_argument("project", type=Path)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--mode", choices=["ask", "fix", "test"], default="fix")
    task_args(export)
    run = commands.add_parser("run-task", help="run an uploaded task inside a notebook session")
    run.add_argument("bundle", type=Path)
    run.add_argument("--destination", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument(
        "--resume",
        action="store_true",
        help="resume an existing workspace; its task manifest must match the bundle",
    )
    run.add_argument(
        "--resume-result",
        type=Path,
        help="restore a previously downloaded result into a fresh task workspace",
    )
    provider_args(run)
    review = commands.add_parser(
        "import-result", help="preview, or import to a separate Git branch"
    )
    review.add_argument("project", type=Path)
    review.add_argument("bundle", type=Path)
    review.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            from .app import serve as serve_app

            serve_app(args.data_dir, args.port, demo=args.demo, open_browser=args.open_browser)
        elif args.command == "doctor":
            from .readiness import setup_report
            from .workspace import save_json

            report = setup_report()
            if args.output:
                save_json(args.output, report)
            print(json.dumps(report, indent=2))
            return 0 if report["status"] == "tools_found" else 2
        elif args.command == "validate-model":
            from .validation import save_report, validate_model

            report = validate_model(model(args), benchmark_dir=args.benchmark_dir)
            save_report(args.output, report)
            print(json.dumps(report, indent=2))
            return 0 if report["status"] == "passed" else 2
        elif args.command == "init":
            print(initialize(args.project))
        elif args.command == "export-task":
            value = export_task(args.project, task(args, args.mode), args.output)
            print(json.dumps({"task_id": value["task_id"], "bundle": str(args.output)}, indent=2))
        elif args.command == "import-result":
            print(json.dumps(import_result(args.project, args.bundle, apply=args.apply), indent=2))
        elif args.command == "run-task":
            if args.resume and args.resume_result:
                raise ValueError("choose --resume or --resume-result, not both")
            if args.resume:
                from .bundles import read_archive, validate_manifest

                root = args.destination.resolve() / "project"
                manifest = json.loads((root / ".aegaeon" / "task-manifest.json").read_text())
                expected = validate_manifest(
                    json.loads(read_archive(args.bundle)["manifest.json"]), "task"
                )
                if manifest != expected:
                    raise ValueError("resume workspace belongs to a different task")
            else:
                root, manifest = unpack_task(args.bundle, args.destination)
            run_task = dict(manifest["task"])
            if args.resume_result:
                previous = restore_checkpoint(root, manifest, args.resume_result)
                run_task["resume_notes"] = {
                    "summary": previous.get("summary", ""),
                    "unresolved": previous.get("unresolved", []),
                }
            session = Session(
                root, run_task, model(args), allow_checks=args.allow_checks, on_event=event
            )
            checkpoint = session.run()
            pack_result(root, manifest, checkpoint, args.output)
            print(
                json.dumps(
                    {
                        "status": checkpoint["status"],
                        "summary": checkpoint["summary"],
                        "result": str(args.output),
                    },
                    indent=2,
                )
            )
        else:
            session = Session(
                args.project,
                task(args, args.command),
                model(args),
                allow_checks=args.allow_checks,
                on_event=event,
            )
            checkpoint = session.run()
            print(json.dumps(checkpoint, indent=2))
        return 0
    except (ValueError, RuntimeError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"AEGAEON Lite: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
