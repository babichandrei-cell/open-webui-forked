# Custom Open WebUI Skills & Tools

This directory documents the custom Skills and Tools used by this Open WebUI deployment.

They are application-level workflow components rather than modifications to the Open WebUI core. Keeping their definitions and architecture in the repository gives the project a versioned source of truth and makes it possible to reconstruct the working environment without relying on the Open WebUI database alone.

## Canonical source layout

```text
custom/
├── README.md
├── skills/
│   ├── image_style_extractor.md
│   ├── imagine.md
│   └── visual-identification--web-verification.md
└── tools/
    ├── image_style_library.py
    ├── image_style_workspace.py
    ├── google_vision_reverse_image_search.py
    └── searxng_image_search.py
```

The files in `custom/skills` contain the Skill instruction bodies used by Open WebUI. The files in `custom/tools` contain the Python Tool source bodies used by Open WebUI. Open WebUI's database remains the live runtime copy; this directory is the Git-tracked canonical copy for development, review, backup, and reconstruction.

When a Skill or Tool is changed in the Open WebUI UI, its canonical repository copy should be updated in the same development session. Conversely, when a repository copy is changed first, the corresponding runtime component must be updated explicitly in Open WebUI before treating the change as deployed.

## Skills

### `image_style_extractor`

Extracts a reusable visual-style profile from one or more reference images.

Its central design rule is strict separation between **style** and **content**:
people, objects, locations, actions, source-specific composition and narrative
information must not leak into the reusable style profile. The extractor
supports photographic and non-photographic media, multi-reference synthesis,
conservative technical inference, provenance lookup, and extensive validation
passes.

Extraction and publication are deliberately separate operations.

Current workflow:

1. analyze the current reference image set;
2. extract and validate a content-independent visual style profile;
3. freeze the visual analysis;
4. resolve provenance through `reverse_image_search_all` when the Google
   Vision Tool is available;
5. create a non-published draft with `create_image_style_draft`;
6. snapshot the exact unique reference-image bytes into draft storage;
7. show the complete profile to the user;
8. wait for explicit approval, revision, or discard;
9. use `update_image_style_draft` for requested revisions;
10. publish only after explicit approval with `publish_image_style_draft`;
11. remove the draft after successful publication.

Invoking the Skill authorizes extraction and draft creation. It does **not**
authorize publication.

A provenance lookup failure does not prevent extraction. In that case the
draft uses `provenance_status = "unknown"`.

Files Workspace may supply current reference images, but it is a source
provider only. It is not the publication destination for image styles.

Depends on:

- `Image Style Library` Tool;
- `Google Vision Reverse Image Search` Tool for provenance resolution.

Canonical source: `custom/skills/image_style_extractor.md`.

### `imagine`

Image-generation workflow optimized for Krea 2.

The user's prompt defines **what is depicted**. A saved style profile defines **how it is rendered**. The Skill deliberately prevents a saved style or its reference images from injecting new semantic scene content.

When a style is supplied, the Skill resolves it through `get_image_style`, translates the reusable visual properties into the active medium, builds one coherent Krea 2 prompt, and calls the configured image-generation Tool. When no style is supplied, no style-library lookup occurs.

Depends on:

- `Image Style Library` Tool when a saved style is requested;
- the Open WebUI image-generation Tool/backend.

Canonical source: `custom/skills/imagine.md`.

### `visual-identification--web-verification`

Strict reverse-image identification and factual-research workflow.

The Skill intentionally forbids using the model's own visual guess as the identity. It first calls `reverse_image_search`. Only a successful reverse-image result may become the identity used for subsequent web research. If reverse matching fails, the workflow stops instead of compensating with speculative visual or text-based searching.

Normal sequence:

1. `reverse_image_search`;
2. one focused web search using the returned identity;
3. optionally fetch one authoritative page;
4. answer the user's actual question.

Depends on:

- `Google Vision Reverse Image Search` Tool;
- Open WebUI web-search/fetch capabilities.

Canonical source: `custom/skills/visual-identification--web-verification.md`.

## Tools

### `image_style_library`

Persistent global visual-style library with an explicit draft/review layer.

Published styles are stored under:

```text
/srv/image_styles/<style_id>/
├── style.json
└── references/
```

Non-published extraction drafts are stored separately under:

```text
/srv/image_style_drafts/<draft_id>/
├── draft.json
└── references/
```

Current public lifecycle functions include:

- `create_image_style_draft` — creates a non-published draft and snapshots the
  exact current reference-image bytes;
- `update_image_style_draft` — updates the profile while preserving the
  existing reference snapshot;
- `publish_image_style_draft` — validates and publishes an approved draft into
  the global Image Style Library;
- `discard_image_style_draft` — removes an abandoned non-published draft;
- `list_image_styles` — returns compact searchable published-style entries;
- `get_image_style` — resolves and returns one complete published profile.

Legacy direct-save behavior must not be used by the current
`image_style_extractor` approval workflow.

Important properties:

- global library storage is independent of Files Workspace;
- Files Workspace is accepted as an authorized source of reference images;
- filesystem references are resolved through trusted Open WebUI request
  context rather than model-supplied host paths or credentials;
- complete reference bytes are read before hashing or snapshotting;
- reference-image MIME/signature validation;
- SHA-256 reference deduplication;
- when the same Files Workspace image also appears as a transient multimodal
  data URL, the Files Workspace representation has deduplication priority so
  original source metadata is retained;
- draft references are immutable across ordinary profile revisions;
- publication uses the draft snapshot rather than requiring historical
  attachments to remain available;
- atomic publication into the global library;
- provenance metadata is separated from visual-style data;
- ambiguous style lookup is reported instead of guessed;
- successful publication removes the consumed draft.

Canonical source: `custom/tools/image_style_library.py`.

### `google_vision_reverse_image_search`

Uses Google Cloud Vision Web Detection for real reverse-image matching.

Public functions:

- `reverse_image_search` — identifies the current image;
- `reverse_image_search_all` — checks every image in the current reference set, primarily for provenance resolution.

The Tool accepts images from the current multimodal message, Open WebUI file
attachments, and authorized Files Workspace filesystem attachments.

Filesystem attachments are resolved through trusted Open WebUI context. The
Tool validates the active Files Workspace connection and current-user access,
fetches the complete binary through the authenticated Files Workspace view
route, and validates the resulting image signature. Model-supplied host paths,
workspace bearer credentials, and arbitrary base URLs are not trusted.

A result is considered identified only when a sufficiently specific identity
is accompanied by matching-image evidence. Generic labels alone are
deliberately rejected.

Runtime dependency:

- environment variable `GOOGLE_CLOUD_VISION_API_KEY`.

The API key must never be committed to this repository.

Canonical source: `custom/tools/google_vision_reverse_image_search.py`.

### `searxng_image_search`

Image-search helper backed by the local SearXNG instance.

Public functions:

- `search_images` — returns image-search candidates and metadata;
- `inspect_search_image` — downloads one selected candidate and returns it as a vision input so the model can actually inspect the image.

Default local SearXNG endpoint: `http://127.0.0.1:8888`.

The Tool deliberately distinguishes search-result metadata from visual inspection: titles and URLs are not treated as evidence of visual similarity.

Canonical source: `custom/tools/searxng_image_search.py`.

## Architectural relationships

```text
Files Workspace / current attachments
              │
              ▼
      reference images
              │
              ▼
     image_style_extractor
              │
              ├── provenance ─────────► google_vision_reverse_image_search
              │
              ▼
   create_image_style_draft
              │
              ▼
 /srv/image_style_drafts
              │
              ▼
      full profile preview
              │
       ┌──────┼────────┐
       │      │        │
     revise approve  discard
       │      │        │
       ▼      │        ▼
 update draft │   discard draft
       │      │
       └──────┘
              │
              ▼
 publish_image_style_draft
              │
              ▼
       /srv/image_styles
              │
              ▼
            imagine
              │
              ▼
    image generation backend


attached image / Files Workspace image
              │
              ▼
visual-identification--web-verification
              │
              ▼
google_vision_reverse_image_search
              │
              ▼
         web research


searxng_image_search
              │
              ├── search_images
              └── inspect_search_image ─────► model vision context
```

### Storage boundaries

These storage layers are intentionally distinct:

```text
Files Workspace
    = project/user file source

/srv/image_style_drafts
    = temporary non-published review state

/srv/image_styles
    = global published Image Style Library
```

Do not use Files Workspace as the publication backend for extracted styles.

## Deprecated/experimental component

`custom/tools/image_style_workspace.py` remains in the repository as an
earlier Files Workspace publication experiment.

Its publication model is **not** the current architecture and it must not be
used by `image_style_extractor`.

The current architecture publishes approved styles only through
`image_style_library` into the global `/srv/image_styles` library.

The file is retained temporarily for review/history and may be removed in a
separate cleanup change after confirming that no runtime component depends on
it.

## Runtime versus repository state

The repository copies are not loaded automatically by Open WebUI merely because they exist here. There are two distinct layers:

```text
Git repository canonical copy
        ↕ explicit synchronization
Open WebUI database/runtime copy
```

This separation is intentional for now. It makes changes reviewable and version-controlled without coupling Open WebUI startup to repository files.

If automatic synchronization is ever added, it should be treated as a separate deployment feature with explicit conflict and rollback semantics rather than silently overwriting runtime definitions.

## Repository policy

- Keep secrets, API keys and environment files out of Git.
- Treat the definitions in this directory as the version-controlled canonical copies of our custom Skills and Tools.
- When a Skill or Tool is changed in Open WebUI, update its repository copy as part of the same development step.
- Runtime data such as `/srv/image_styles`, Open WebUI databases, generated images and Project Files workspace contents do not belong in this directory.
- Tool source should contain safe defaults only; deployment-specific secrets belong in protected environment configuration.
- Export JSON from Open WebUI may be used as a transport/backup format, but the canonical repository copy should remain the readable Skill Markdown or Tool Python source unless there is a concrete reason to version the full export envelope.

## Current compatibility

The current workflow was developed and tested against the custom Open WebUI 0.11.3 fork in this repository.
