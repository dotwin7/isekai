from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .parser import DIRECT_RUNTIME_ACTIONS, build_parser as _parser
from .installation import configure_installed_profiles, doctor_project
from .runtime_request import runtime_exit_code, runtime_request
from ..distribution import (
    install_from_bootstrap_checkout,
    install_from_git,
    load_install_lock,
    plan_git_update,
    rollback_install,
    verify_distribution,
    write_distribution_manifest,
)
from ..foundation import FoundationError, load_foundation
from ..runtime_contract import dispatch
from ..mcp_server import serve_mcp
from ..support.locking import LockUnavailable
from ..workflow import WorkRoute, resolve_context, unit_status, verify_unit


def _json(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "host-custody":
        # Compatibility for early development builds. Keep the implementation
        # term out of public help and route recovery through the user-facing
        # project doctor instead.
        legacy = argparse.ArgumentParser(prog="isekai host-custody")
        legacy.add_argument(
            "--runtime", choices=("kiro", "claude", "codex"), required=True
        )
        legacy.add_argument("--path", default=".")
        legacy.add_argument("--apply", action="store_true")
        legacy_args = legacy.parse_args(arguments[1:])
        arguments = ["doctor", "--path", legacy_args.path]
        if legacy_args.apply:
            arguments.append("--fix")
    if arguments and arguments[0] in DIRECT_RUNTIME_ACTIONS:
        arguments = ["runtime", *arguments]
    args = _parser().parse_args(arguments)
    try:
        if args.command == "install":
            if args.checkout:
                installed = install_from_bootstrap_checkout(
                    args.checkout,
                    args.source,
                    args.ref,
                    args.path,
                    runtimes=args.runtime or ("all",),
                    adopt_foundation=args.adopt_foundation,
                )
            else:
                installed = install_from_git(
                    args.source,
                    args.ref,
                    args.path,
                    runtimes=args.runtime or ("all",),
                    adopt_foundation=args.adopt_foundation,
                )
            _json(configure_installed_profiles(args.path, installed))
        elif args.command == "update":
            lock = load_install_lock(args.path)
            if lock is None:
                raise ValueError("cannot update before ISEKAI is installed")
            source = args.source or lock.get("source", {}).get("git")
            if not isinstance(source, str) or not source:
                raise ValueError("update requires --source or a Git source in isekai.lock.json")
            runtimes = args.runtime or tuple(lock.get("adapters", {}))
            if args.check:
                _json(
                    plan_git_update(
                        source,
                        args.ref,
                        args.path,
                        runtimes=runtimes,
                        include_foundation=args.include_foundation,
                    )
                )
            else:
                installed = install_from_git(
                    source,
                    args.ref,
                    args.path,
                    runtimes=runtimes,
                    update=True,
                    include_foundation=args.include_foundation,
                    adopt_foundation=args.adopt_foundation,
                )
                _json(configure_installed_profiles(args.path, installed))
        elif args.command == "doctor":
            result = doctor_project(args.path, fix=args.fix)
            _json(result)
            return 0 if result["ready"] else 1
        elif args.command == "rollback":
            _json(rollback_install(args.path))
        elif args.command == "distribution-build":
            path = write_distribution_manifest(args.root, args.output)
            _json({"created": str(path)})
        elif args.command == "distribution-check":
            result = verify_distribution(args.root)
            _json(result)
            return 0 if result["valid"] else 1
        elif args.command == "validate":
            _json({"valid": True, "foundation": load_foundation(args.foundation).summary()})
        elif args.command == "resolve":
            _json(resolve_context(args.project, WorkRoute(args.route)))
        elif args.command == "unit-status":
            _json(unit_status(args.unit_dir))
        elif args.command == "unit-verify":
            result = verify_unit(args.unit_dir)
            _json(result)
            return 0 if result["valid"] else 1
        elif args.command == "structure":
            root = Path(args.root).resolve()
            files = [
                str(path.relative_to(root))
                for base in ("foundation", "src", "tests")
                if (root / base).exists()
                for path in sorted((root / base).rglob("*"))
                if path.is_file() and "__pycache__" not in path.parts
            ]
            _json({"root": str(root), "files": files})
        elif args.command == "dispatch":
            from ..dispatch import dispatch_loop
            result = dispatch_loop(
                args.project,
                work_dir=args.unit,
                initial_prompt=args.prompt,
                max_iterations=args.max_iterations,
            )
            _json(result)
            return 0 if result.get("completed") else 1
        elif args.command == "runtime":
            action, payload = runtime_request(args)
            result = dispatch(action, payload)
            _json(result)
            return runtime_exit_code(action, result)
        elif args.command == "mcp-serve":
            return serve_mcp(args.project, runtime=args.runtime)
        return 0
    except (FoundationError, ValueError, FileExistsError, LockUnavailable) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
