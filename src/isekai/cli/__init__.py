from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Sequence

from .parser import DIRECT_PLUGIN_ACTIONS, _parser
from .plugin_request import plugin_exit_code, plugin_request
from ..distribution import (
    doctor_install,
    install_from_bootstrap_checkout,
    install_from_git,
    load_install_lock,
    plan_git_update,
    rollback_install,
    verify_distribution,
    write_distribution_manifest,
)
from ..foundation import FoundationError, load_foundation
from ..plugin_contract import dispatch
from ..support.locking import LockUnavailable
from ..workflow import WorkRoute, resolve_context, unit_status, verify_unit


def _json(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in DIRECT_PLUGIN_ACTIONS:
        arguments = ["plugin", *arguments]
    args = _parser().parse_args(arguments)
    try:
        if args.command == "install":
            if args.checkout:
                _json(
                    install_from_bootstrap_checkout(
                        args.checkout,
                        args.source,
                        args.ref,
                        args.path,
                        runtimes=args.runtime or ("all",),
                        adopt_foundation=args.adopt_foundation,
                    )
                )
            else:
                _json(
                    install_from_git(
                        args.source,
                        args.ref,
                        args.path,
                        runtimes=args.runtime or ("all",),
                        adopt_foundation=args.adopt_foundation,
                    )
                )
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
                _json(
                    install_from_git(
                        source,
                        args.ref,
                        args.path,
                        runtimes=runtimes,
                        update=True,
                        include_foundation=args.include_foundation,
                        adopt_foundation=args.adopt_foundation,
                    )
                )
        elif args.command == "doctor":
            result = doctor_install(args.path)
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
        elif args.command == "plugin":
            action, payload = plugin_request(args)
            result = dispatch(action, payload)
            _json(result)
            return plugin_exit_code(action, result)
        return 0
    except (FoundationError, ValueError, FileExistsError, LockUnavailable) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
