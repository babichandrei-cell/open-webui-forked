# Reverse Image Identification & Web Research

Use this workflow whenever the user asks to identify, recognize, locate, or
research a real-world place, building, landmark, artwork, monument, object,
vehicle, product, or other physical subject shown in an attached image.

## Purpose

Use a real reverse-image lookup to establish the identity first.

Only after the identity is established should normal web research be used to
answer questions about the subject.

Do not simulate reverse-image search through visual guessing or textual
descriptions.

## Core rule

Your own visual recognition is not the identification method.

Do not begin by guessing the object's name.

Do not convert visible features into search queries in an attempt to imitate a
reverse-image search.

The attached image must first be submitted to:

`reverse_image_search`

## Required workflow

### 1. Reverse-image identification

Call:

`reverse_image_search`

before calling:

- `search_web`
- `fetch_url`

The tool receives the user's attached image automatically.

Do not provide the tool with a candidate name or textual visual description.

### 2. Inspect the reverse-image result

Read the returned `found` field.

---

## If `found` is false

Stop the identification workflow.

Do NOT:

- guess the identity yourself;
- identify the object from visual intuition;
- describe possible candidates;
- search the web using visible features;
- call `search_images`;
- generate candidate names;
- search for similar buildings, products, artworks, vehicles, or objects;
- use `search_web` as a substitute for reverse-image search.

Reply briefly that the image could not be reliably identified and that
reliable information could not be found from the image.

If `technical_error` is true, state that reverse-image identification failed
because of a technical problem.

Do not present a speculative answer.

---

## If `found` is true

Treat the returned `identity` as the reverse-image identification result.

Do not replace it with another candidate based on your own visual impression.

Do not restart image identification.

Proceed directly to factual research about the returned identity.

## 3. Perform one focused web search

Call `search_web` once using the exact returned identity.

The query should normally be:

`<identity>`

or, when useful:

`<identity> official history`

Do not perform several differently phrased searches unless the first search
fails to provide usable results.

The purpose of `search_web` is factual research, not image identification.

## 4. Prefer authoritative sources

From the search results, prefer sources in this order:

1. official site responsible for the subject;
2. government, heritage, museum, university, manufacturer, organization, or
   other primary source;
3. strong independent reference source;
4. Wikipedia only when a stronger source is unavailable or when it provides
   useful supplementary context.

Do not collect many redundant sources.

## 5. Fetch at most one page by default

Use `fetch_url` only when the search snippets are insufficient for answering
the user's question reliably.

By default:

- fetch at most ONE authoritative page;
- prefer the official or primary source;
- do not fetch both Wikipedia and an official source merely to accumulate more
  information.

A second `fetch_url` is allowed only when:

- the first source lacks information directly required by the user;
- important sources conflict;
- the identity is ambiguous;
- the user explicitly requests detailed or deep research.

Do not fetch additional pages merely because they are available.

## 6. Keep research proportional to the question

For a simple request such as:

"Please recognize this building and tell me about it."

Provide a concise overview containing only useful information such as:

- canonical name;
- location;
- what it is;
- approximate date or period;
- creator, architect, owner, or historical association when relevant;
- why it is notable.

Do not produce an exhaustive history unless requested.

For a more specific question, research only the facts needed to answer it.

## 7. Do not reinterpret the image after identification

`search_web` and `fetch_url` research facts about the identity returned by
`reverse_image_search`.

They are not another image-identification stage.

Do not change the identity merely because another object seems visually
plausible.

If factual research reveals genuine ambiguity in the returned name, explain
that ambiguity instead of silently substituting another identity.

## 8. Final answer

When `found` is true:

1. state the identified subject;
2. give its location when relevant;
3. briefly mention that it was identified through reverse-image matching;
4. answer the user's actual question;
5. cite the best authoritative sources;
6. avoid unnecessary research detail.

Do not expose internal tool JSON unless the user asks for it.

## Efficiency rule

The preferred sequence is:

1. `reverse_image_search`
2. `search_web` once
3. optionally `fetch_url` once
4. answer

A normal successful identification should not require a long agentic search
loop.

## Strict failure rule

No reliable reverse-image result means no identification.

Do not compensate for a failed reverse-image result with visual speculation or
text-based image searching.