# Imagine

Generate an image from the user's request using the available image generation tool.

---

## Input Contract

The `/imagine` request uses this structure:

{
  "style": string | null,
  "prompt": string
}

`prompt` is required.

`style` is optional.

Treat `style` as not specified when it is:

- omitted;
- null;
- an empty string;
- whitespace only.

Do not require additional fields.

---

## Core Principle

The user's `prompt` defines **what is depicted**.

A retrieved style profile defines **how it is visually rendered**.

These are separate sources of authority.

The user's semantic content always takes priority.

A saved style must never introduce new semantic content merely to express
its visual characteristics.

**Style controls visual treatment. It does not supply scene inventory.**

---

## Workflow

When this skill is invoked:

1. Parse the request according to the Input Contract.
2. Identify the user's required `prompt`.
3. Determine whether a saved `style` is specified.
4. If no style is specified, proceed directly to normal prompt construction.
5. If a style is specified, retrieve it using `get_image_style`.
6. Keep the user's semantic content and the retrieved visual style logically separate.
7. Apply the transferable visual properties of the saved style to the user's existing content.
8. Transform the result into one coherent natural-language prompt optimized for Krea 2.
9. Call the available `generate_image` tool with that prompt.
10. Verify that an actual image was returned before reporting success.

The purpose of this skill is to generate the image.

Do not stop after preparing or displaying a prompt when image generation is available.

---

## Saved Style Resolution

When `style` is missing, null, empty, or whitespace only:

- do not call `list_image_styles`;
- do not call `get_image_style`;
- do not infer a saved style from conversation context;
- generate directly from the user's prompt.

When `style` contains a non-empty value:

- call `get_image_style` using the supplied value as the lookup query;
- use only a successfully retrieved saved profile;
- never reconstruct or imitate a missing saved style from model knowledge.

### Found

If `get_image_style` returns:

`status = "found"`

use the retrieved style profile.

### Not Found

If `get_image_style` returns:

`status = "not_found"`

do not silently substitute another style.

Do not invent an interpretation of the requested style.

Do not generate until the style reference is resolved or the user chooses to continue without it.

### Ambiguous

If `get_image_style` returns:

`status = "ambiguous"`

do not select a candidate automatically.

Ask the user to specify one of the returned `style_id` values.

---

## Semantic Authority

The user's prompt is authoritative for:

- entities;
- identities;
- subject classes;
- physical characteristics;
- clothing;
- objects;
- props;
- actions;
- poses;
- environment;
- location;
- architecture;
- weather;
- time of day;
- historical period;
- narrative situation;
- relationships;
- visible text;
- any other semantic property explicitly requested by the user.

Preserve these faithfully.

Do not replace, reinterpret, or override explicit semantic information merely
to better match the selected style.

---

## Unspecified Content

**Unspecified does not mean freely inventable.**

When an important semantic property is not specified by the user, preserve
that openness whenever possible.

Do not independently assign a distinctive semantic identity merely because
additional prompt detail could be added.

Do not unnecessarily invent:

- additional entities;
- additional people;
- additional objects;
- additional props;
- additional light-emitting objects;
- additional architecture;
- additional environmental features;
- additional scenery;
- additional weather;
- additional time-of-day conditions;
- additional actions;
- additional narrative context;
- specific brands;
- specific models;
- specific eras;
- specific cultural identities;
- specific design categories.

Prefer describing the visual appearance of content already requested rather
than introducing new content.

---

## Controlled Visual Expansion

The user's prompt may be expanded when additional detail helps visually realize
something the user has already specified.

Expansion should elaborate **appearance and rendering**, not semantic identity.

Appropriate expansion may include:

- visible material response;
- surface texture;
- spatial depth;
- physically plausible light interaction;
- motion appearance;
- atmospheric rendering of already specified conditions;
- framing;
- camera relationship;
- focus behavior;
- tonal behavior;
- color rendering;
- image texture;
- visual mood.

Do not turn a broad semantic category into a more specific semantic category
unless the user requested that specificity.

Do not create new scene elements merely to make the prompt more detailed.

---

## Content Preservation

Do not remove explicit user details merely because they do not appear in the
saved style profile.

Do not add details merely because they appeared in the original style
reference images.

Do not use the style profile to reinterpret the user's content.

The generated scene should still clearly be the scene described by the user
if all stylistic language were removed from the final prompt.

---

## Medium Authority

The rendering medium is a first-order visual constraint.

When a retrieved saved style clearly specifies a medium or rendering mode,
that medium must govern the entire generated image unless the user explicitly
requests a conflicting medium.

Examples of medium classes include:

- photographic;
- oil painting;
- watercolor;
- gouache;
- ink drawing;
- charcoal;
- pastel;
- cel animation;
- painterly digital illustration;
- graphic illustration;
- photorealistic 3D rendering.

Do not treat a strong medium specification as a decorative effect applied
after constructing the scene.

The complete image — forms, edges, materials, depth, highlights, shadows,
surface detail, and background — must be rendered through that medium.

When the saved style is non-photographic, avoid allowing photographic
rendering conventions to dominate merely because the requested subject
could naturally be depicted as a photograph.

---

## Cross-Medium Translation

Interpret style-profile properties through the active rendering medium.

Do not mechanically preserve photographic terminology when applying a
non-photographic style.

Translate transferable visual principles into medium-appropriate language.

For non-photographic rendering:

- optical depth should become painterly or graphic spatial separation;
- focus falloff should become progressive simplification, softness, or
  abstraction of distant forms;
- optical softness should become edge softness appropriate to the medium;
- surface microtexture should be expressed through the medium's mark-making
  or material character;
- camera perspective should be expressed as viewpoint and composition unless
  photographic camera language is explicitly useful;
- highlight diffusion should be expressed through the rendering behavior of
  the medium rather than simulated lens effects.

Do not introduce photographic concepts such as:

- lens rendering;
- bokeh;
- photographic sharpness;
- camera optics;
- sensor-like detail;
- photographic depth of field;

when they conflict with the selected non-photographic medium.

Preserve the underlying visual principle while translating its expression
into the active medium.

---

## Medium Conflict Suppression

When the selected style has a clearly non-photographic medium, remove or
rewrite prompt language that would pull generation toward photographic
realism unless the user explicitly requires it.

Do not allow generic prompt-enhancement habits to reintroduce an incompatible
rendering mode.

The final prompt should contain one coherent rendering model.

If necessary to preserve the selected medium, explicitly reinforce that the
image is fully rendered in that medium rather than photorealistically
rendered and subsequently stylized.

---

## Style Isolation

A saved style profile contains transferable visual treatment.

It must not supply semantic content to the new image.

Never import from the saved style or its provenance:

- people;
- characters;
- actors;
- celebrities;
- occupations;
- clothing;
- objects;
- props;
- vehicles;
- architecture;
- locations;
- environments;
- actions;
- narrative events;
- scene-specific arrangements;
- source-work imagery.

The saved style name is a library lookup key.

After the style has been retrieved, do not use its provenance title as
generation guidance.

Do not include phrases whose purpose is to imitate the source work by name.

Use the extracted visual profile instead.

---

## Style Must Not Create Content

A saved style may modify the appearance of elements already present in the
user's requested scene.

It must not create new semantic elements merely to satisfy a stylistic
property.

If a style calls for a visual characteristic such as:

- localized warm accents;
- bright practical-like highlights;
- deep shadow separation;
- geometric visual rhythm;
- atmospheric depth;
- edge illumination;
- reflected color;

express that characteristic through compatible existing scene content or
through non-semantic visual treatment whenever possible.

Do not invent a new object, fixture, environmental feature, architectural
element, or narrative element solely to produce a stylistic effect.

If a stylistic property cannot be naturally expressed without introducing
new semantic content, reduce or omit that property rather than changing the
scene.

---

## Style Application

When a saved style is retrieved, use its visual properties to shape the new image.

Use:

`generation_guidance`

as the primary generation-oriented style prescription.

Use the following fields selectively when they materially improve integration:

- `style_identity`;
- `medium`;
- `lighting`;
- `color_palette`;
- `tonal_response`;
- `texture`;
- `optics`;
- `depth_of_field`;
- `composition`;
- `atmosphere`;
- `style_keywords`;
- `avoid`.

Do not mechanically concatenate every field.

Do not copy the complete style profile into the image prompt.

Do not repeat the same visual property merely because it appears in multiple
style fields.

Synthesize the relevant properties into a compact and coherent visual treatment.

---

## Style Keywords

`style_keywords` represent a compact visual fingerprint.

Use them only when they reinforce important characteristics of the selected
style.

Do not dump them into the final prompt as a keyword list.

Integrate useful characteristics naturally into the prompt.

---

## Negative Style Guidance

Treat `avoid` as guidance about visual treatment.

It must not remove or contradict semantic content explicitly requested by the user.

Apply negative guidance only to properties such as:

- lighting treatment;
- tonal response;
- saturation;
- color grading;
- sharpness;
- texture;
- bloom;
- contrast;
- optical rendering;
- other stylistic characteristics.

Semantic content has priority over negative stylistic guidance.

---

## Reference Independence

A retrieved saved style may contain `reference_files`.

These are provenance and extraction references.

For the current workflow:

- do not inspect them;
- do not pass them to `generate_image`;
- do not derive additional content from them;
- do not copy their compositions;
- do not reconstruct their scenes.

Use only the structured textual style profile.

Direct reference-image conditioning, if introduced in the future, is a
separate workflow.

---

## Krea 2 Prompt Construction

Transform the user's request and any retrieved style profile into one polished
natural-language prompt optimized for Krea 2.

Preserve the user's semantic intent and explicit constraints.

Expand only within the semantic boundaries established by the user.

Prefer vivid, visually useful prose over keyword lists.

Use visual detail where it clarifies:

- appearance;
- material;
- pose or action already requested;
- composition;
- framing;
- depth;
- lighting;
- texture;
- color;
- atmosphere;
- medium.

Do not increase semantic specificity merely to make the prompt longer.

Do not invent major or minor scene elements simply because they would make
the image visually richer.

If the original prompt is already detailed, refine it lightly rather than
rewriting or expanding it aggressively.

If the user requests visible text, preserve the exact wording.

Do not add:

- safety policy text;
- disclaimers;
- tool instructions;
- metadata;
- JSON terminology;
- explanations;
- meta commentary.

---

## Prompt Structure

When the active style is photographic or medium-neutral, start directly with
the user's requested subject or scene and integrate visual treatment
throughout the prompt.

When the active style specifies a strong non-photographic medium, establish
that medium and its defining rendering character before describing the
subject.

Use approximately this organization:

**Active medium and defining rendering language → requested content →
requested appearance and action → composition/viewpoint → requested
environment → lighting and color treatment → tonal, textural, and spatial
rendering → atmosphere**

Do not treat the medium as a closing style suffix.

The prompt must make the requested content appear inherently created in the
selected medium, not as realistic content with the medium applied afterward.

---

## Tool Use

After preparing the final prompt, call:

`generate_image`

Pass the complete optimized Krea 2 prompt as the image-generation prompt.

Do not ask the user to manually copy the prompt into an image generator.

Do not stop after displaying or describing the prompt.

If image generation is available, complete the workflow by calling the tool.

---

## Generation Result

After calling `generate_image`, inspect the returned result.

Treat generation as successful only when:

- the tool reports success; and
- the returned `images` collection contains at least one image.

A successful status with an empty image collection is not a successful image generation.

If no image was returned:

- do not claim that an image was generated;
- report that the generation backend returned no image.

Only acknowledge successful generation when at least one actual generated
image was returned.

---

## Final Validation

Before calling `generate_image`, silently verify the following.

### Semantic Integrity Test

Ask:

> Does every meaningful semantic addition in the final prompt originate from
> the user's request or follow necessarily from it?

Remove unnecessary semantic additions.

---

### Unspecified Content Test

Ask:

> Did I turn anything the user left unspecified into a distinctive new
> semantic choice?

If yes, generalize or remove that choice.

---

### Content Preservation Test

Check that:

- every explicit user requirement is preserved;
- no requested entity or property was replaced to better fit the style;
- the scene still represents the user's request independently of the style.

---

### Style Isolation Test

Check that:

- the saved style contributes visual treatment only;
- no semantic content from provenance or reference images entered the prompt;
- the style name itself is not being used as image-generation guidance.

---

### Style Inventory Test

Ask:

> Did I create any scene element solely because the saved style needed a
> particular visual effect?

If yes, remove the invented element and express the style through existing
content or omit that stylistic property.

---

### Style Resolution Test

If `style` was specified:

- `get_image_style` was called;
- the requested style was successfully resolved;
- no invented fallback style was used.

If no style was specified:

- no saved-style lookup occurred.

---

### Prompt Quality Test

Verify that the final prompt:

- begins with the requested content;
- is coherent natural language;
- remains visually descriptive;
- does not contain raw JSON;
- does not contain a raw style-field dump;
- does not mention library internals;
- does not contain unnecessary semantic invention;
- is concise enough that important user content is not diluted by style text.

Then call `generate_image`.