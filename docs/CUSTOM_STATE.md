# Custom Fork State

Last updated: 2026-09-06

This document records the current working production state of this Open WebUI fork and the surrounding Files Workspace integration. It is intended as a handoff/context checkpoint so work can continue safely in a new chat without reconstructing the implementation from memory.

## Production checkpoint

Open WebUI version: `0.11.3`

Repository: `babichandrei-cell/open-webui-forked`

Production source checkout:

```text
/srv/open-webui-forked
```

Current code checkpoint before this state document:

```text
336dc9cdd Filter folder files workspace connections
ebf64b231 Fix files workspace none state and dev reloads
e38b81379 Add files workspace provisioning from folders
480d907ac Add folder files workspace inheritance to chats
40e888a25 Add files workspace binding to folder modal
```

At cutover, local `main`, `origin/main`, and `origin/HEAD` all pointed to `336dc9cdd`, and the working tree was clean.

The custom fork is now the primary production Open WebUI instance. The previous packaged `uvx open-webui@0.11.3` process is no longer running.

## Production service

Systemd service:

```text
/etc/systemd/system/open-webui.service
```

Current service identity:

```text
Description=Open WebUI - Custom Fork
WorkingDirectory=/srv/open-webui-forked
```

Production process:

```text
/srv/open-webui-forked/.venv/bin/python -m uvicorn open_webui.main:app --host 0.0.0.0 --port 3000
```

Production port:

```text
0.0.0.0:3000
```

At the production verification checkpoint, only port `3000` was listening among `3000`, `3001`, and `5050`. The temporary backend on `3001` and Vite dev frontend on `5050` were stopped.

Important production environment:

```text
DATA_DIR=/srv/open-webui/data
FRONTEND_BUILD_DIR=/srv/open-webui-forked/build
PYTHONPATH=/srv/open-webui-forked/backend
PROJECT_FILES_API_URL=http://127.0.0.1:8003
```

Secrets are not stored in this repository. Production secret values are supplied from protected server-side files/environment.

Protected custom fork environment file:

```text
/etc/open-webui/custom-fork.env
```

It contains the production `WEBUI_SECRET_KEY`, `PROJECT_FILES_API_URL`, and `PROJECT_FILES_ADMIN_KEY`. Permissions were set to root-only (`0600`). Do not commit or print those values.

Existing Google Vision drop-in remains active:

```text
/etc/systemd/system/open-webui.service.d/google-vision.conf
```

which loads:

```text
/etc/open-webui/vision.env
```

Rollback copy of the previous service unit:

```text
/etc/systemd/system/open-webui.service.pre-custom-fork
```

The old `/srv/open-webui` directory is intentionally retained. Its production data directory remains the active data source, and it also provides rollback safety.

## Production frontend build

Frontend is built from the fork using:

```bash
cd /srv/open-webui-forked
NODE_OPTIONS="--max-old-space-size=12288" npm run build
```

The larger Node heap is required because a default build hit the V8 heap limit at roughly 4 GB during chunk rendering.

Successful build output is written to:

```text
/srv/open-webui-forked/build
```

The backend serves that directory directly through `FRONTEND_BUILD_DIR`; Vite is not required in production.

## Production data

Active Open WebUI data directory:

```text
/srv/open-webui/data
```

Active SQLite database:

```text
/srv/open-webui/data/webui.db
```

SQLite is operating with WAL files, so changes may reside in `webui.db-wal` and timestamps/sizes of the base `webui.db` alone must not be used to judge freshness.

A development/test copy also exists:

```text
/srv/open-webui-forked/test-data
```

This is not production data and should not replace the production database wholesale.

A read-only comparison after cutover showed production contained two chats absent from the test copy:

```text
Webcam Role-Play
Test Message
```

and two tags absent from the test copy:

```text
Adult Content
Role-play
```

Therefore production is the authoritative database. Do not replace it with `test-data`.

The model table showed `26` production rows versus `29` test rows, but comparing by model `id` produced no IDs present only on one side. This suggests duplicate IDs or another row-level difference inside the test copy rather than three clearly missing production models. No database migration is required based on the current evidence; investigate only if a concrete missing model is noticed in the UI.

## Files Workspace goal

The project implements a restricted per-project filesystem workflow for Open WebUI.

Desired model:

```text
OWUI Folder / Project
        |
        `-- optional Files Workspace binding
              |-- None
              |-- create a new registered workspace
              `-- select an existing registered workspace
```

The filesystem is deliberately not a general-purpose shell or code-execution environment.

Core security principle:

```text
Model can read/write/manage files only inside an explicitly registered workspace.
Model cannot execute shell commands.
Model cannot attach arbitrary existing host directories.
```

Arbitrary host-path attachment is intentionally out of scope. A directory created/copied outside this workflow is not later bound into Open WebUI as a workspace. Workspaces are created and managed through the Open WebUI/Project Files flow.

## Project Files API

Service root:

```text
/srv/project-files-api
```

Workspace storage root:

```text
/srv/owui-projects
```

API address exposed to Open WebUI:

```text
http://127.0.0.1:8003
```

Project Files API uses bearer-token-to-workspace resolution. Each workspace token resolves to exactly one registered root below `/srv/owui-projects`.

The browser/model never supplies an arbitrary host path.

Registry:

```text
/srv/project-files-api/data/workspaces.json
```

Container mappings/concepts:

```text
/srv/owui-projects -> /projects:rw
./data             -> /data:rw
PROJECTS_ROOT=/projects
WORKSPACE_REGISTRY=/data/workspaces.json
```

Admin provisioning uses a separate `PROJECT_FILES_ADMIN_KEY`, stored server-side and not exposed to the model/browser.

Workspace registry stores SHA-256 token hashes rather than plaintext workspace tokens. The plaintext token is returned only once when a workspace is created and is then stored by Open WebUI as the terminal connection bearer key.

Path traversal and symlink escape are protected by resolving requested paths and verifying they remain relative to the selected workspace root.

### Model-visible Files API

The registered workspace OpenAPI exposes file operations but no shell execution:

```text
GET  /api/config
POST /files/copy
POST /files/delete
GET  /files/list
POST /files/mkdir
POST /files/move
GET  /files/read
POST /files/write
GET  /ports
```

The Project Files API reports:

```json
{"features":{"terminal":false,"notebooks":false,"system":false}}
```

A request to `/api/config` without a workspace bearer token correctly returns HTTP `401`.

Additional UI-compatible routes support File Browser operations such as upload, download/view, rename/move, delete, folder creation, file creation, and editing.

## Open WebUI workspace representation

Open WebUI Terminal Server Connections are currently used as the Open WebUI-facing registry of Files Workspaces.

A Folder stores only the connection identifier:

```json
{
  "workspace": {
    "connection_id": "<terminal connection id>"
  }
}
```

Folder data must never store workspace host paths, workspace bearer tokens, or the Project Files admin key.

Actual connection credentials remain in Open WebUI configuration.

Files Workspace selector filters connections to filesystem-capable entries only, using:

```text
config.files_workspace == true
OR
config.chat_uploads == "filesystem"
```

This keeps unrelated terminal connections out of the Folder workspace selector.

## Implemented Open WebUI changes

### 1. Folder Files Workspace binding

File:

```text
src/lib/components/layout/Sidebar/Folders/FolderModal.svelte
```

Folder settings now contain a `Files Workspace` selector.

Supported current states:

```text
None
registered filesystem workspace
```

The selected connection ID is stored in `folder.data.workspace.connection_id`.

Saving an edited currently-active Folder switches the active Files Workspace immediately without requiring navigation away and back.

### 2. Folder inheritance in chats

File:

```text
src/lib/components/chat/Chat.svelte
```

When entering a Folder or loading a chat belonging to a Folder, the Folder's Files Workspace connection becomes the active `selectedTerminalId`.

Embedded/Notes chat contexts do not mutate the global filesystem context.

Current conceptual rule:

```text
Folder = Project
Files Workspace = Project filesystem
Chats inside the Folder inherit that filesystem
```

### 3. No Chat override yet

A per-chat workspace override was discussed but intentionally not implemented.

Reason: allowing a chat inside Project A to point at Project B's filesystem blurs project boundaries and complicates the conceptual model without a demonstrated use case.

If a real need appears later, consider a narrow override such as `Inherit from Folder` versus `Disable Files Workspace` before allowing arbitrary cross-project workspace selection.

### 4. Correct `None` semantics

File:

```text
src/lib/components/chat/FileNav.svelte
```

Previously, null `selectedTerminalId` fell back to the first terminal connection, so `None` did not actually mean no filesystem.

That fallback was removed.

Current behavior:

```text
selectedTerminalId == null
=> no filesystem workspace
=> Files tab disappears
=> no hidden fallback to another connection
```

This was tested in the UI.

### 5. Files Workspace provisioning from Folder UI

Frontend files:

```text
src/lib/components/layout/Sidebar/Folders/FolderModal.svelte
src/lib/apis/configs/index.ts
```

Backend file:

```text
backend/open_webui/routers/configs.py
```

Admin users see:

```text
+ Create Workspace
```

The frontend calls:

```text
POST /api/v1/configs/files_workspaces
```

with only the requested workspace name.

The Open WebUI backend then calls the Project Files API admin provisioning endpoint using the server-side admin key, receives the new workspace ID/token, creates a new Terminal Server Connection, and returns only non-secret identifiers to the browser.

The newly created connection is refreshed into the frontend and automatically selected in the Folder modal.

Open WebUI connection metadata includes:

```json
{
  "chat_uploads": "filesystem",
  "files_workspace": true,
  "workspace_id": "<workspace id>"
}
```

### 6. Vite development reload fix

File:

```text
vite.config.ts
```

During development Vite watched `test-data/webui.db-wal`, causing full-page Open WebUI reloads whenever SQLite wrote to the WAL.

`test-data` is now ignored by the Vite watcher.

### 7. Local development artifacts ignored

`.gitignore` includes local test data and backup file patterns used during this work. `test-data/` must remain uncommitted.

## Verified UI behavior

The following behavior has been manually verified:

- Production frontend loads from the static build without Vite.
- Existing Folder can display its assigned Files Workspace.
- File Browser shows workspace files and folders.
- Selecting `None` and saving removes the Files workspace from that Folder and hides the Files tab.
- Re-selecting the workspace restores File Browser immediately.
- Folder workspace selector contains only filesystem-capable connections.
- `+ Create Workspace` is visible in Folder settings for an admin.
- Files Workspace label appears in chat input when a workspace is active.
- Existing files can be listed and previewed.
- Upload, editing/save, folder creation, file creation, rename/move, delete, and image preview were tested during development.

## Existing workspace examples

At least these development/registered workspaces have existed during testing:

```text
Test Project Files
Isolation Test Workspace
Provisioning Test Workspace
UI Test Workspace
```

These names are examples/current test artifacts, not architecture requirements.

`Test Project Files` was used for the main manual integration test.

## Development environment

Source checkout:

```text
/srv/open-webui-forked
```

Python virtual environment:

```text
/srv/open-webui-forked/.venv
```

Node/npm used during development:

```text
Node v22.23.2
npm 10.9.8
```

Temporary backend during development:

```text
127.0.0.1:3001
```

Temporary Vite frontend during development:

```text
0.0.0.0:5050
```

Both are stopped in the production checkpoint.

If development is resumed, do not point the dev backend directly at the live production SQLite database while production Open WebUI is running. Use `test-data` or another safe copy.

## Known caveats / deferred hardening

The current implementation is working and intentionally kept simple. Known items that may be revisited later:

1. Project Files workspace creation and Open WebUI connection persistence are not transactional across both services. If workspace creation succeeds but Open WebUI config persistence fails, an orphan workspace can remain.
2. Project Files registry helper locking does not currently wrap the complete admin read-modify-write transaction. This is acceptable for the current single-user/low-concurrency deployment but can be hardened later.
3. Existing pre-created symlinks inside workspace storage could deserve additional metadata/listing hardening even though path resolution protects file operations and the model has no symlink-creation endpoint.
4. Newly provisioned Open WebUI connections work without explicitly adding every optional terminal metadata field such as `server_type` or `policy_id`; these can be normalized later if upstream behavior requires it.
5. No workspace delete/deprovision endpoint was added initially.
6. No Chat-level Files Workspace override is implemented by design.
7. Arbitrary existing host-directory attachment is out of scope by design.

## Upstream/update strategy

This repository is now the production fork, so future Open WebUI upgrades must preserve/rebase the custom patches rather than switching production back to the packaged upstream `uvx` install.

Before an upstream update:

1. Record current production commit.
2. Back up production data and systemd configuration.
3. Fetch/rebase or merge upstream into the fork in a development/test environment.
4. Rebuild the frontend with an adequate Node heap.
5. Run a temporary backend against test data.
6. Verify Files Workspace functionality.
7. Only then restart the production service on the new fork commit.

Do not use `npm audit fix --force` as part of routine update work.

## Recommended next development step

Do not add features merely because they are conceivable. Continue from real use and testing.

Current Files Workspace architecture should be treated as the working baseline:

```text
Folder = Project
Folder may have one registered Files Workspace or None
Chats inherit Folder workspace
No arbitrary host path attachment
No shell/code execution
```

Potential future additions should be driven by concrete workflow problems observed in daily use.

## Quick production verification commands

```bash
systemctl status open-webui --no-pager -l

sudo ss -ltnp | grep ':3000'

curl -fsS http://127.0.0.1:3000/api/version

ps -ef | grep -E 'open-webui|uvicorn open_webui' | grep -v grep

cd /srv/open-webui-forked
git status --short
git log -5 --oneline --decorate
```

Expected production process pattern:

```text
/srv/open-webui-forked/.venv/bin/python -m uvicorn open_webui.main:app --host 0.0.0.0 --port 3000
```

The old pattern below should not be running:

```text
uvx --python 3.11 open-webui@0.11.3 serve --host 0.0.0.0 --port 3000
```

## Handoff note

When continuing this work in a new chat, read this file first and treat it as the authoritative implementation checkpoint. Confirm live server state before making changes because runtime configuration may have changed after this document was written.
