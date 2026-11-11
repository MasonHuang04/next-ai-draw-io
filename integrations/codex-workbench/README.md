# Optional local Codex integration

[中文说明](README.zh-CN.md)

This fork branch adds a local Codex connection without changing the default Next AI Draw.io provider flow. It has two independent entry points:

| Entry point | What it does |
| --- | --- |
| `compatibility_bridge.py` | A loopback OpenAI-compatible Chat Completions endpoint for the original Next AI Draw.io UI. |
| `server.py` + Electron/UI files | A separate drawing workbench with recent Codex conversations, persistent history, live responses, approvals, cancellation and file synchronization. |

Both use the installed Codex CLI and its existing sign-in. They do not contain a shared API key or an exported Codex login. Codex performs its normal model requests; account access, network behavior and usage limits still apply.

## Requirements

- Python 3.11+, Node.js and npm.
- An installed Codex CLI. Sign in using its normal supported flow before using this integration.
- For the workbench, a local draw.io web asset directory. Use the copy in an installed Windows Next AI Draw.io app, set `DRAWIO_ASSETS`, or prepare this repository's copy with the existing root setup (`npm ci`, then `node scripts/fetch-drawio.mjs`).

Windows is the locally validated desktop platform. The launcher resolves Electron's platform-specific binary, but macOS/Linux desktop behavior is not claimed to be validated here.

## A. Connect the original Next AI Draw.io UI

From this integration directory:

```sh
python compatibility_bridge.py
```

In another terminal, explicitly display this installation's local connection details:

```sh
python compatibility_bridge.py --connection-info
```

In Next AI Draw.io, open **Settings → Models → OpenAI**, expand the base URL options, and configure:

- Base URL: `http://127.0.0.1:61428/v1`
- Model: `codex-current`
- API-key field: the `local_connection_key` from the command above. This is a generated loopback connection key, not an OpenAI API key. Do not post its value in issues or commit it.

Add the model and use the UI's connection test. The adapter accepts `/v1/models` and `/v1/chat/completions`, including diagram tool-call responses and SSE-shaped responses. This compatibility mode runs a bounded ephemeral Codex request for each completion. It does **not** share Codex Desktop conversation history, and its SSE response is emitted after the structured completion is ready rather than mirroring token deltas.

## B. Open the conversation workbench

```sh
cd integrations/codex-workbench
npm ci
npm start
```

The canvas stays editable by hand. The left **Recent conversations** list reads the real local Codex history; choose a conversation or start one in a project folder. The list is searchable and collapsible. Each conversation retains its own unsent draft.

When attaching the drawing, the workbench saves the current `.drawio` file and includes its path and editing context in the user message. Codex modifies that file; the canvas reloads its changes. Concurrent unsaved edits receive a conflict prompt instead of silently overwriting the other version.

## How the server connection was added

```text
Original UI → localhost Chat Completions adapter → codex exec → model

Workbench UI → authenticated localhost service → Codex app-server JSON-RPC
                                                   ├─ thread/list, thread/read
                                                   ├─ thread/turns/list
                                                   ├─ thread/queue/add
                                                   └─ live events, approval responses, turn/interrupt
```

`codex_rpc.py` owns the normal app-server subprocess connection. If another client holds a conversation's writer, the workbench submits through the official shared queue instead of trying to take over that writer.

When the workbench owns execution, its UI receives incremental messages, handles supported approvals and can stop the active reply. When Codex Desktop owns execution through a separate stdio connection, this integration reads persisted history and submits to the same queue; it cannot mirror that other connection's unpersisted token stream or answer that connection's approval callbacks. More filesystem permission does not change this transport boundary.

## Runtime state and configuration

| Setting | Purpose |
| --- | --- |
| `CODEX_CLI_PATH` | Optional path to the user's Codex executable. Otherwise resolve it from PATH or the normal Windows install. |
| `CODEX_HOME` | The user's normal Codex home, when overridden. |
| `DRAWIO_ASSETS` | Path to a local draw.io web asset directory. |
| `DRAWIO_PYTHON` | Python executable for the desktop service. The launcher sets this automatically. |
| `CODEX_BRIDGE_STATE` | Optional state directory for the compatibility endpoint. Default: `state/compatibility-bridge`. |
| `CODEX_BRIDGE_PORT` | Compatibility endpoint port. Default: `61428`. |
| `CODEX_WORKBENCH_PORT` | Workbench service port. Default: `61429`; set the same environment for its launcher and developer restart. |

The workbench binds to `127.0.0.1:61429`. Both services require their generated local connection key for model/conversation APIs. `state/`, drawings, logs, browser profiles and test artifacts are ignored by Git.

## Privacy and publication scope

The source does handle personal data at runtime: local conversation text/titles, selected project paths, drafts and drawing contents. Selected prompts and drawing context are processed by the user's normal Codex installation. This does not make those data part of the published repository.

This branch includes source, generic tests and documentation only. It excludes login files, generated keys, environment files, conversation IDs/transcripts, local screenshots, diagrams, machine-specific launchers and local verification records. See [PRIVACY.md](PRIVACY.md) for the publication review and the automated source check.

## Checks and development

```sh
python -m unittest discover -s tests -p "test_*.py"
npm run check
python tests/check_publishable.py
```

The unit checks use synthetic data and mock Codex output; they do not require a login, submit prompts, or access a real conversation. The publication check inspects Git-tracked integration files, not the user's runtime directories.

After changing UI files, reload the workbench. After changing Python service code, use `npm run restart-service`. The service refuses to interrupt its own active conversation during a developer restart.

The integration follows this repository's Apache-2.0 license. The draw.io web assets remain under their upstream license and are not copied into this source addition. Protocol reference: [Codex App Server](https://learn.chatgpt.com/docs/app-server).
