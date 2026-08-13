# ISEKAI Claude Code adapter

This adapter follows Claude Code's documented project Skill layout:

```text
runtime/adapters/claude/
└── skills/isekai/SKILL.md
```

For a Project install, the bootstrap with `--runtime claude` copies the versioned Skill to `.claude/skills/isekai/`, records its digest in `isekai.lock.json`, merges Project-local denies for Edit, Write, NotebookEdit, and Bash, and connects the Project Core MCP server in one flow. It creates no marketplace registration or lifecycle hook. `doctor --fix` repairs the Project execution guard when needed. Start a new conversation after installation.

Invoke the project Skill in the Claude Code session:

```text
/isekai on --project path/to/project.json
/isekai off
```

The Adapter is discoverable but ISEKAI mode is off by default in each new conversation. Discovery, Skill installation, a leftover cache, and repository contents are not activation. A textual mention of a command is not activation either and must not trigger the Skill. `disable-model-invocation: true` prevents implicit loading. Only an intentional `/isekai <action>` is a one-shot invocation while mode is off. `on` activates Project-level intake routing for later requests, `resume` separately restores a Unit, and `off` stops routing without writing artifacts or checkpoints.

Claude Code transports an explicitly selected Skill separately from its argument string. The Skill binds that string with `$ARGUMENTS`; therefore `/isekai off` may reach the active Skill as the argument `off` without another literal `/isekai` in the model-visible text. The Adapter treats the host-marked Skill load as authoritative and executes the action instead of asking the user to repeat it. Reading or pasting `SKILL.md` remains a non-invocation.

`unit-init` or `resume` creates a Project-scoped Core binding that remains active until the final Operation Decision transitions the Unit to `learned`. Before then, Core blocks new routing, new Unit creation, and persistent sibling-Unit actions; follow-up additions and changes use `amend` in the same Unit. Only an explicit user decision to start separate work, abandon the Unit, or switch Units permits `active-unit-detach` after a current Checkpoint. `off` does not clear this binding.

Start Claude Code from a repository containing `project.json` to use `on` without a path. Core searches ancestors and unambiguous descendant candidates. If no manifest exists, the `init --path PATH` action creates a validated manifest and Project-local `units/` after explicit confirmation; multiple candidates require user selection. Sensitive raw Evidence belongs under ignored `units/**/evidence/raw/`.

The Skill requires the selected Project launcher `.isekai/bin/isekai`, never falls back to a global executable, and performs a handshake that also verifies the Project execution guard. Direct Claude write and shell tools remain denied. Unit documents use `artifact_write`, Project changes use `managed_edit`, and tests use `prove`; ISEKAI Core performs authorization and execution as one receipted action. Free-standing `authorize edit|test` calls are denied. L2 external API access still uses an opaque `secret://provider/name` resolved by the host; production, deployment, and arbitrary high-risk actions remain prohibited.

Claude's Bash or file permission prompt authorizes a tool invocation, not a lifecycle Decision. Before `amend`, `active-unit-detach`, `decision`, `foundation-decision`, or `foundation-promote`, the Adapter presents the bound subject and waits for an explicit user response. `status` and `resume` expose the next required lifecycle Decision as `human_gate`; a non-interactive or bypass-permission session cannot invent that approval.

## Compatibility

There is currently no linked live-verified Claude CLI baseline. Local CLI `2.1.224` passed the project Skill source contract check, but no authenticated model session was available, so it remains `validation-only`. The former `2.1.220` repository claim is retained only as `legacy_versions` because no raw smoke record is linked. After a CLI upgrade, run `python scripts/runtime-host-check.py --runtime claude --require-cli` and `scripts/live-smoke.py --runtime claude --host claude`; the latter resumes the same session for automatic intake and runs the completed-Unit Golden Path before recording Evidence.
