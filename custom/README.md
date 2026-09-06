# Custom Open WebUI Skills & Tools

This directory documents the custom Skills and Tools used by this Open WebUI deployment.

They are application-level workflow components rather than modifications to the Open WebUI core. Keeping their definitions and architecture in the repository gives the project a versioned source of truth and makes it possible to reconstruct the working environment without relying on the Open WebUI database alone.

## Skills

### `image_style_extractor`

Extracts a reusable visual-style profile from one or more reference images.

Its central design rule is strict separation between **style** and **content**: people, objects, locations, actions, source-specific composition and narrative information must not leak into the reusable style profile. The extractor supports photographic and non-photographic media, multi-reference synthesis, conservative technical inference, provenance lookup, and validation passes before saving.

Workflow:

1. analyze the current reference image set;
2. extract and validate a content-independent style profile;
3. freeze the visual analysis;
4. optionally resolve provenance through `reverse_image_search_all`;
5. save the style through `save_image_style`.

Depends on:

- `Image Style Library` Tool;
- `Google Vision Reverse Image Search` Tool for optional provenance resolution.

### `imagine`

Image-generation workflow optimized for Krea 2.

The user's prompt defines **what is depicted**. A saved style profile defines **how it is rendered**. The Skill deliberately prevents a saved style or its reference images from injecting new semantic scene content.

When a style is supplied, the Skill resolves it through `get_image_style`, translates the reusable visual properties into the active medium, builds one coherent Krea 2 prompt, and calls the configured image-generation Tool. When no style is supplied, no style-library lookup occurs.

Depends on:

- `Image Style Library` Tool when a saved style is requested;
- the Open WebUI image-generation Tool/backend.

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

## Tools

### `image_style_library`

Persistent local visual-style library stored under `/srv/image_styles`.

Public functions:

- `save_image_style` — atomically saves a structured style profile and the reference images from the latest user message;
- `list_image_styles` — returns compact searchable catalog entries;
- `get_image_style` — resolves and returns one complete saved profile.

Important properties:

- Unicode-safe filesystem IDs;
- reference-image MIME/signature validation;
- SHA-256 reference deduplication;
- atomic publication through a temporary directory;
- provenance metadata separated from visual-style data;
- no remote image fetching during save;
- ambiguous style lookup is reported instead of guessed.

### `google_vision_reverse_image_search`

Uses Google Cloud Vision Web Detection for real reverse-image matching.

Public functions:

- `reverse_image_search` — identifies the current image;
- `reverse_image_search_all` — checks every image in the current reference set, primarily for provenance resolution.

The Tool accepts images from the current multimodal message and can fall back to Open WebUI file attachments. A result is considered identified only when a sufficiently specific identity is accompanied by matching-image evidence. Generic labels alone are deliberately rejected.

Runtime dependency:

- environment variable `GOOGLE_CLOUD_VISION_API_KEY`.

The API key must never be committed to this repository.

### `searxng_image_search`

Image-search helper backed by the local SearXNG instance.

Public functions:

- `search_images` — returns image-search candidates and metadata;
- `inspect_search_image` — downloads one selected candidate and returns it as a vision input so the model can actually inspect the image.

Default local SearXNG endpoint: `http://127.0.0.1:8888`.

The Tool deliberately distinguishes search-result metadata from visual inspection: titles and URLs are not treated as evidence of visual similarity.

## Architectural relationships

```text
reference images
      │
      ▼
image_style_extractor
      │
      ├──── optional provenance ───► google_vision_reverse_image_search
      │
      ▼
image_style_library
      │
      ▼
/srv/image_styles
      │
      ▼
imagine ───────────────────────────► image generation backend

attached image
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

## Repository policy

- Keep secrets, API keys and environment files out of Git.
- Treat the definitions in this directory as the version-controlled canonical copies of our custom Skills and Tools.
- When a Skill or Tool is changed in Open WebUI, update its repository copy as part of the same development step.
- Runtime data such as `/srv/image_styles`, Open WebUI databases, generated images and Project Files workspace contents do not belong in this directory.
- Tool source should contain safe defaults only; deployment-specific secrets belong in protected environment configuration.

## Current compatibility

The current workflow was developed and tested against the custom Open WebUI 0.11.3 fork in this repository.