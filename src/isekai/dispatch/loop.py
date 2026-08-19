"""Dispatch loop — select agent per phase, launch, detect transition, repeat."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from ..support.logging import LOGGER, configure_logging
from .broker import build_handoff
from .config import load_dispatch_config, resolve_model, ESCALATION_DEFAULTS
from .runners import RUNNERS, RunResult

_ACTIVE_BINDING_RELATIVE = ".isekai-runtime/active-unit.json"


TERMINAL_STATUSES = frozenset({"learned", "abandoned"})
MAX_CONSECUTIVE_FAILURES = 5


def _select_agent_and_model(
    dispatch_config: dict[str, Any],
    phase: str,
    consecutive_failures: int = 0,
) -> tuple[str, str]:
    phase_config = dispatch_config.get("phase_dispatch", {}).get(phase)
    if phase_config is None:
        default_agent = dispatch_config.get("default_agent", "claude")
        return default_agent, resolve_model(default_agent, "default")

    agent = phase_config["agent"]
    model = phase_config["model"]

    escalation = dispatch_config.get("escalation", ESCALATION_DEFAULTS)
    threshold = escalation.get("consecutive_failures", 2)
    if consecutive_failures >= threshold:
        escalation_tier = escalation.get("tier_escalation", "strong")
        escalated_model = resolve_model(agent, escalation_tier)
        if escalated_model != model:
            LOGGER.info(
                "escalating model after %d failures: %s -> %s",
                consecutive_failures, model, escalated_model,
            )
            model = escalated_model

    return agent, model


def _build_resume_prompt(handoff: dict[str, Any], skill_content: str | None) -> str:
    parts: list[str] = []
    parts.append(f"Resume ISEKAI Unit {handoff.get('unit_id')} in phase '{handoff.get('phase')}'.")
    parts.append("")

    next_action = handoff.get("next_action")
    if next_action:
        parts.append(f"Next action: {next_action}")

    pending = handoff.get("pending", [])
    if pending:
        parts.append(f"Pending: {', '.join(str(p) for p in pending)}")

    completed = handoff.get("completed", [])
    if completed:
        parts.append(f"Completed: {', '.join(str(c) for c in completed)}")

    parts.append("")
    parts.append("Execute these steps in order:")
    parts.append("1. /isekai on")
    parts.append(f"2. /isekai resume --unit {handoff.get('unit_id', 'PATH')}")
    parts.append("3. Continue with the next action above.")
    parts.append("4. Write a Checkpoint when the phase work is done or a transition is needed.")
    parts.append("5. Wrap up the session after the Checkpoint.")

    if skill_content:
        parts.append("")
        parts.append("--- Phase Skill ---")
        parts.append(skill_content)

    return "\n".join(parts)


def _prompt_human_gate(handoff: dict[str, Any]) -> bool:
    status = handoff.get("status", "")
    phase = handoff.get("phase", "")
    unit_id = handoff.get("unit_id", "unknown")
    blocked_by = handoff.get("blocked_by", [])

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"HUMAN GATE — Unit {unit_id}", file=sys.stderr)
    print(f"Status: {status}", file=sys.stderr)
    print(f"Phase: {phase}", file=sys.stderr)
    if blocked_by:
        print(f"Blocked by: {', '.join(str(b) for b in blocked_by)}", file=sys.stderr)
    print("", file=sys.stderr)
    print("The agent has prepared a Decision Packet and is waiting", file=sys.stderr)
    print("for your review. Inspect the Unit artifacts before deciding.", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    while True:
        try:
            answer = input("\nContinue with next agent session? [y/n/q]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no", "q", "quit"):
            return False
        print("Please enter y, n, or q.", file=sys.stderr)


def _discover_active_unit(project_root: Path) -> Path | None:
    binding_path = project_root / _ACTIVE_BINDING_RELATIVE
    if not binding_path.is_file():
        return None
    try:
        import json
        data = json.loads(binding_path.read_text(encoding="utf-8"))
        unit_path = data.get("unit", {}).get("path")
        if isinstance(unit_path, str):
            candidate = Path(unit_path)
            if candidate.is_dir() and (candidate / "unit.json").is_file():
                return candidate
    except Exception:
        pass
    return None


def dispatch_loop(
    project_root: str | Path,
    *,
    work_dir: str | Path | None = None,
    initial_prompt: str | None = None,
    max_iterations: int = 20,
) -> dict[str, Any]:
    configure_logging()
    project_root = Path(project_root).expanduser().resolve()
    dispatch_config = load_dispatch_config(project_root)

    iterations = 0
    consecutive_failures = 0
    last_phase: str | None = None
    history: list[dict[str, Any]] = []

    while iterations < max_iterations:
        iterations += 1

        resolved_work_dir = Path(work_dir).expanduser().resolve() if work_dir else None
        if resolved_work_dir is None:
            discovered = _discover_active_unit(project_root)
            if discovered is not None:
                resolved_work_dir = discovered
                work_dir = discovered
                LOGGER.info("discovered active Unit: %s", discovered)
        handoff = build_handoff(resolved_work_dir) if resolved_work_dir else None

        if handoff is not None:
            current_phase = handoff.get("phase", "")
            current_status = handoff.get("status", "")

            if current_status in TERMINAL_STATUSES:
                LOGGER.info("Unit reached terminal status: %s", current_status)
                return {
                    "completed": True,
                    "final_status": current_status,
                    "iterations": iterations - 1,
                    "history": history,
                }

            if handoff.get("human_gate_pending"):
                LOGGER.info("Human Gate pending at status: %s", current_status)
                if not _prompt_human_gate(handoff):
                    return {
                        "completed": False,
                        "stopped_at": "human_gate",
                        "status": current_status,
                        "phase": current_phase,
                        "iterations": iterations - 1,
                        "history": history,
                    }

            agent_name, model = _select_agent_and_model(
                dispatch_config, current_phase, consecutive_failures
            )

            if last_phase and current_phase != last_phase:
                LOGGER.info(
                    "phase transition: %s -> %s, agent: %s, model: %s",
                    last_phase, current_phase, agent_name, model,
                )
                consecutive_failures = 0

            skill_content: str | None = None
            skill_path = handoff.get("stage_skill")
            if isinstance(skill_path, str):
                try:
                    skill_content = Path(skill_path).read_text(encoding="utf-8")
                except OSError:
                    pass

            prompt = _build_resume_prompt(handoff, skill_content)
        else:
            agent_name = dispatch_config.get("default_agent", "claude")
            model = resolve_model(agent_name, "default")
            prompt = initial_prompt or ""
            current_phase = "unknown"

        runner_cls = RUNNERS.get(agent_name)
        if runner_cls is None:
            LOGGER.error("unknown agent: %s, falling back to claude", agent_name)
            runner_cls = RUNNERS["claude"]
            agent_name = "claude"

        runner = runner_cls()
        if not runner.is_available():
            LOGGER.error("%s CLI not available", agent_name)
            return {
                "completed": False,
                "error": f"{agent_name} CLI not found",
                "iterations": iterations,
                "history": history,
            }

        LOGGER.info(
            "dispatch iteration %d: phase=%s agent=%s model=%s",
            iterations, current_phase, agent_name, model,
        )

        result = runner.run(project_root, model=model, prompt=prompt)

        history.append({
            "iteration": iterations,
            "phase": current_phase,
            "agent": agent_name,
            "model": model,
            "exit_code": result.exit_code,
        })

        if result.exit_code != 0:
            consecutive_failures += 1
            LOGGER.warning(
                "agent exited with code %d (failure %d/%d)",
                result.exit_code, consecutive_failures, MAX_CONSECUTIVE_FAILURES,
            )
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                LOGGER.error("max consecutive failures reached, stopping")
                return {
                    "completed": False,
                    "error": "max consecutive failures",
                    "iterations": iterations,
                    "history": history,
                }
        else:
            consecutive_failures = 0

        last_phase = current_phase

    LOGGER.warning("max iterations reached: %d", max_iterations)
    return {
        "completed": False,
        "error": "max iterations reached",
        "iterations": iterations,
        "history": history,
    }
