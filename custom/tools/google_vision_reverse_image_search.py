"""
title: Google Vision Reverse Image Search
description: Identifies one or more images attached to the current user message using Google Cloud Vision Web Detection.
author: local
version: 1.1.0
"""

import asyncio
import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from open_webui.models.files import Files


class Tools:
    VISION_URL = "https://vision.googleapis.com/v1/images:annotate"

    # Safe raw size for one Base64 image inside Google's REST JSON body.
    MAX_RAW_IMAGE_BYTES = 7_000_000

    # Safety limit for one multi-reference invocation.
    MAX_BATCH_IMAGES = 16

    # Separate requests are used for multi-reference lookup so that several
    # Base64 images do not collectively exceed Google's REST body limit.
    MAX_CONCURRENT_REQUESTS = 4

    GENERIC_LABELS = {
        "architecture",
        "art",
        "building",
        "car",
        "castle",
        "cathedral",
        "church",
        "flower",
        "garden",
        "house",
        "landmark",
        "landscape",
        "manor",
        "monument",
        "mountain",
        "museum",
        "object",
        "official residence",
        "palace",
        "park",
        "person",
        "plant",
        "sculpture",
        "tourist attraction",
        "vehicle",
    }

    def __init__(self):
        pass

    # ================================================================
    # IMAGE HELPERS
    # ================================================================

    @staticmethod
    def _decode_data_url(value: str) -> bytes | None:
        if not isinstance(value, str):
            return None

        if not value.startswith("data:image/"):
            return None

        try:
            header, encoded = value.split(",", 1)

            if ";base64" not in header.lower():
                return None

            return base64.b64decode(
                encoded,
                validate=False,
            )
        except Exception:
            return None

    @staticmethod
    def _looks_like_image(data: bytes) -> bool:
        if not data:
            return False

        if data.startswith(b"\xff\xd8\xff"):
            return True

        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return True

        if data.startswith((b"GIF87a", b"GIF89a")):
            return True

        if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return True

        if data.startswith(b"BM"):
            return True

        return False

    @staticmethod
    def _extract_item_name(
        item: dict,
        image_url: Any,
        fallback: str,
    ) -> str:
        candidates = [
            item.get("name"),
            item.get("filename"),
        ]

        if isinstance(image_url, dict):
            candidates.extend(
                [
                    image_url.get("name"),
                    image_url.get("filename"),
                ]
            )

        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return Path(candidate).name

        return fallback

    @staticmethod
    def _extract_image_url_value(
        item: dict,
    ) -> tuple[str | None, Any]:
        item_type = item.get("type")

        if item_type == "image_url":
            image_url = item.get("image_url")

            if isinstance(image_url, dict):
                value = image_url.get("url")
            else:
                value = image_url

            return (
                value if isinstance(value, str) else None,
                image_url,
            )

        if item_type in ("input_image", "image"):
            image_url = item.get("image_url") or item.get("url")

            if isinstance(image_url, dict):
                value = image_url.get("url")
            else:
                value = image_url

            return (
                value if isinstance(value, str) else None,
                image_url,
            )

        return None, None

    # ================================================================
    # PRIMARY SOURCE: CURRENT MULTIMODAL MESSAGE
    # ================================================================

    def _find_images_in_messages(
        self,
        messages: list[dict] | None,
    ) -> list[dict[str, Any]]:
        """
        Return all valid image inputs from the most recent user message
        that contains images.

        Images are returned in message order.
        """

        for message in reversed(messages or []):
            if not isinstance(message, dict):
                continue

            if message.get("role") != "user":
                continue

            content = message.get("content")

            if not isinstance(content, list):
                continue

            images: list[dict[str, Any]] = []

            for item in content:
                if not isinstance(item, dict):
                    continue

                value, image_url = self._extract_image_url_value(item)

                if not value:
                    continue

                data = self._decode_data_url(value)

                if not data or not self._looks_like_image(data):
                    continue

                index = len(images) + 1

                images.append(
                    {
                        "data": data,
                        "filename": self._extract_item_name(
                            item,
                            image_url,
                            f"attached-image-{index:03d}",
                        ),
                        "image_source": "messages",
                    }
                )

            if images:
                return images

        return []

    # ================================================================
    # FALLBACK SOURCE: OPEN WEBUI ATTACHMENT
    # ================================================================

    @staticmethod
    def _get_file_id(item: dict) -> str | None:
        value = item.get("id")

        if value:
            return str(value)

        nested = item.get("file")

        if isinstance(nested, dict) and nested.get("id"):
            return str(nested["id"])

        return None

    async def _read_file_attachment(
        self,
        item: dict,
        user_id: str | None,
    ) -> tuple[bytes | None, str | None]:
        direct_url = item.get("url")

        if isinstance(direct_url, str):
            data = self._decode_data_url(direct_url)

            if data and self._looks_like_image(data):
                return (
                    data,
                    item.get("name") or "attached-image",
                )

        nested = item.get("file")

        if isinstance(nested, dict):
            file_data = nested.get("data")

            if isinstance(file_data, dict):
                content = file_data.get("content")

                if isinstance(content, str):
                    data = self._decode_data_url(content)

                    if data and self._looks_like_image(data):
                        return (
                            data,
                            nested.get("filename")
                            or item.get("name")
                            or "attached-image",
                        )

        file_id = self._get_file_id(item)

        if not file_id or not user_id:
            return None, None

        try:
            file_model = await Files.get_file_by_id_and_user_id(
                file_id,
                user_id,
            )
        except Exception:
            return None, None

        if not file_model or not file_model.path:
            return None, None

        path = Path(file_model.path)

        if not path.is_file():
            return None, None

        try:
            data = await asyncio.to_thread(path.read_bytes)
        except Exception:
            return None, None

        if not self._looks_like_image(data):
            return None, None

        return (
            data,
            file_model.filename or "attached-image",
        )

    async def _find_images_in_files(
        self,
        files: list[dict] | None,
        user_id: str | None,
    ) -> list[dict[str, Any]]:
        images: list[dict[str, Any]] = []

        for item in files or []:
            if not isinstance(item, dict):
                continue

            data, name = await self._read_file_attachment(
                item,
                user_id,
            )

            if not data:
                continue

            images.append(
                {
                    "data": data,
                    "filename": name or f"attached-image-{len(images)+1:03d}",
                    "image_source": "files",
                }
            )

        return images

    async def _find_images(
        self,
        messages: list[dict] | None,
        files: list[dict] | None,
        user_id: str | None,
    ) -> list[dict[str, Any]]:
        images = self._find_images_in_messages(messages)

        if images:
            return images

        return await self._find_images_in_files(
            files,
            user_id,
        )

    async def _find_image(
        self,
        messages: list[dict] | None,
        files: list[dict] | None,
        user_id: str | None,
    ) -> tuple[bytes | None, str | None, str | None]:
        """
        Compatibility helper for the existing single-image function.

        If several images are attached, preserve historical behavior by
        using the final image in the current reference set.
        """

        images = await self._find_images(
            messages,
            files,
            user_id,
        )

        if not images:
            return None, None, None

        image = images[-1]

        return (
            image["data"],
            image["filename"],
            image["image_source"],
        )

    # ================================================================
    # GOOGLE CLOUD VISION
    # ================================================================

    def _vision_request_sync(
        self,
        image_data: bytes,
        api_key: str,
    ) -> dict[str, Any]:
        encoded = base64.b64encode(image_data).decode("ascii")

        payload = {
            "requests": [
                {
                    "image": {
                        "content": encoded,
                    },
                    "features": [
                        {
                            "type": "WEB_DETECTION",
                            "maxResults": 10,
                        }
                    ],
                }
            ]
        }

        body = json.dumps(
            payload,
            separators=(",", ":"),
        ).encode("utf-8")

        request = urllib.request.Request(
            self.VISION_URL,
            data=body,
            method="POST",
            headers={
                "X-Goog-Api-Key": api_key,
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
            },
        )

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        try:
            with opener.open(
                request,
                timeout=45,
            ) as response:
                return json.loads(response.read().decode("utf-8"))

        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode(
                "utf-8",
                errors="replace",
            )

            raise RuntimeError(
                f"Google Vision HTTP {exc.code}: " f"{error_body[:1000]}"
            )

        except urllib.error.URLError as exc:
            raise RuntimeError(f"Google Vision network error: {exc}")

    # ================================================================
    # RESULT INTERPRETATION
    # ================================================================

    def _is_specific_identity(
        self,
        value: str,
    ) -> bool:
        if not value:
            return False

        normalized = value.strip().lower().replace("-", " ").replace("_", " ")

        if not normalized:
            return False

        if normalized in self.GENERIC_LABELS:
            return False

        words = [word for word in normalized.split() if word]

        return len(words) >= 2

    def _choose_identity(
        self,
        web: dict,
    ) -> tuple[str | None, str | None, float | None]:
        for item in web.get("bestGuessLabels") or []:
            label = (item.get("label") or "").strip()

            if self._is_specific_identity(label):
                return (
                    label,
                    "best_guess_label",
                    None,
                )

        entities = sorted(
            web.get("webEntities") or [],
            key=lambda item: float(item.get("score", 0) or 0),
            reverse=True,
        )

        for item in entities:
            description = (item.get("description") or "").strip()

            score = float(item.get("score", 0) or 0)

            if score >= 0.5 and self._is_specific_identity(description):
                return (
                    description,
                    "web_entity",
                    score,
                )

        return None, None, None

    def _interpret_result(
        self,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        if result.get("error"):
            return {
                "found": False,
                "technical_error": True,
                "reason": "Google Vision could not process the image.",
                "error": result["error"],
            }

        web = result.get("webDetection") or {}

        (
            identity,
            identity_source,
            identity_score,
        ) = self._choose_identity(web)

        entities = sorted(
            web.get("webEntities") or [],
            key=lambda item: float(item.get("score", 0) or 0),
            reverse=True,
        )

        top_entities = []

        for item in entities:
            description = (item.get("description") or "").strip()

            if not description:
                continue

            top_entities.append(
                {
                    "description": description,
                    "score": round(
                        float(item.get("score", 0) or 0),
                        4,
                    ),
                }
            )

            if len(top_entities) >= 5:
                break

        matching_pages = []

        for item in web.get("pagesWithMatchingImages") or []:
            url = item.get("url")

            if not url:
                continue

            matching_pages.append(
                {
                    "title": item.get("pageTitle"),
                    "url": url,
                }
            )

            if len(matching_pages) >= 3:
                break

        full_matches = web.get("fullMatchingImages") or []

        partial_matches = web.get("partialMatchingImages") or []

        has_match_evidence = bool(matching_pages or full_matches or partial_matches)

        found = bool(identity and has_match_evidence)

        if found:
            return {
                "found": True,
                "technical_error": False,
                "identity": identity,
                "identity_source": identity_source,
                "identity_score": identity_score,
                "top_entities": top_entities,
                "matching_pages": matching_pages,
                "full_matching_images": len(full_matches),
                "partial_matching_images": len(partial_matches),
            }

        return {
            "found": False,
            "technical_error": False,
            "identity": None,
            "top_entities": top_entities,
            "matching_pages": matching_pages,
            "full_matching_images": len(full_matches),
            "partial_matching_images": len(partial_matches),
            "reason": (
                "Google Vision did not establish a sufficiently "
                "specific identity with matching-image evidence."
            ),
        }

    async def _search_one_image(
        self,
        image_data: bytes,
        api_key: str,
    ) -> dict[str, Any]:
        if len(image_data) > self.MAX_RAW_IMAGE_BYTES:
            return {
                "found": False,
                "technical_error": True,
                "reason": (
                    "The attached image is too large for "
                    "the current Google Vision REST request."
                ),
                "image_bytes": len(image_data),
            }

        try:
            response = await asyncio.to_thread(
                self._vision_request_sync,
                image_data,
                api_key,
            )
        except Exception as exc:
            return {
                "found": False,
                "technical_error": True,
                "reason": "Google Vision request failed.",
                "error": str(exc),
            }

        responses = response.get("responses") or []

        if not responses:
            return {
                "found": False,
                "technical_error": True,
                "reason": "Google Vision returned no response.",
            }

        return self._interpret_result(responses[0])

    # ================================================================
    # PUBLIC TOOL: SINGLE IMAGE
    # ================================================================

    async def reverse_image_search(
        self,
        __messages__: list[dict] | None = None,
        __files__: list[dict] | None = None,
        __user__: dict | None = None,
    ) -> str:
        """
        Identify the real-world subject shown in the current user's attached
        image using Google Cloud Vision Web Detection.

        The actual image is sent to Google. Do not provide a textual guess.

        If `found` is true, use the returned `identity` as the query for
        normal web research.

        If `found` is false, do not guess or substitute textual image search.
        """

        api_key = os.getenv("GOOGLE_CLOUD_VISION_API_KEY")

        if not api_key:
            return json.dumps(
                {
                    "found": False,
                    "technical_error": True,
                    "reason": ("Google Vision API key is not configured."),
                    "instruction": ("Stop. Do not guess the identity."),
                },
                ensure_ascii=False,
            )

        user_id = None

        if isinstance(__user__, dict):
            value = __user__.get("id")

            if value:
                user_id = str(value)

        (
            image_data,
            filename,
            image_source,
        ) = await self._find_image(
            __messages__,
            __files__,
            user_id,
        )

        if not image_data:
            return json.dumps(
                {
                    "found": False,
                    "technical_error": True,
                    "reason": (
                        "The current image could not be accessed "
                        "from the multimodal message or attachment."
                    ),
                    "instruction": (
                        "Stop. Do not guess or substitute "
                        "textual web/image searching."
                    ),
                },
                ensure_ascii=False,
            )

        output = await self._search_one_image(
            image_data,
            api_key,
        )

        output["filename"] = filename
        output["image_source"] = image_source

        if output.get("found"):
            output["instruction"] = (
                "The image was identified by reverse-image matching. "
                "Use exactly this identity for factual web research. "
                "Do not generate alternative visual candidates."
            )
        else:
            output["instruction"] = (
                "Stop. Do not guess. Do not perform feature-based "
                "image searching or generate alternative candidates. "
                "Tell the user the image could not be reliably identified."
            )

        return json.dumps(
            output,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    # ================================================================
    # PUBLIC TOOL: ALL CURRENT REFERENCES
    # ================================================================

    async def reverse_image_search_all(
        self,
        __messages__: list[dict] | None = None,
        __files__: list[dict] | None = None,
        __user__: dict | None = None,
    ) -> str:
        """
        Reverse-search every image attached to the current user reference set.

        Use this for provenance resolution across multiple reference images.

        Compare results conservatively. Matching-image evidence may be used
        to establish a shared source work, but never guess provenance from
        visual appearance.

        A failed or ambiguous provenance lookup must not block an unrelated
        style-extraction workflow; use unknown provenance instead.
        """

        api_key = os.getenv("GOOGLE_CLOUD_VISION_API_KEY")

        if not api_key:
            return json.dumps(
                {
                    "image_count": 0,
                    "found_count": 0,
                    "technical_error": True,
                    "reason": ("Google Vision API key is not configured."),
                    "results": [],
                    "instruction": (
                        "Provenance could not be checked. "
                        "For style workflows, continue with unknown "
                        "provenance and use a descriptive style name."
                    ),
                },
                ensure_ascii=False,
            )

        user_id = None

        if isinstance(__user__, dict):
            value = __user__.get("id")

            if value:
                user_id = str(value)

        images = await self._find_images(
            __messages__,
            __files__,
            user_id,
        )

        if not images:
            return json.dumps(
                {
                    "image_count": 0,
                    "found_count": 0,
                    "technical_error": True,
                    "reason": (
                        "No reference images could be accessed "
                        "from the current multimodal message or attachments."
                    ),
                    "results": [],
                    "instruction": (
                        "Provenance could not be checked. "
                        "For style workflows, continue with unknown "
                        "provenance and use a descriptive style name."
                    ),
                },
                ensure_ascii=False,
            )

        if len(images) > self.MAX_BATCH_IMAGES:
            return json.dumps(
                {
                    "image_count": len(images),
                    "found_count": 0,
                    "technical_error": True,
                    "reason": (
                        f"Too many images for one provenance lookup. "
                        f"Maximum is {self.MAX_BATCH_IMAGES}."
                    ),
                    "results": [],
                    "instruction": (
                        "For style workflows, continue with unknown "
                        "provenance rather than guessing."
                    ),
                },
                ensure_ascii=False,
            )

        semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_REQUESTS)

        async def search_reference(
            index: int,
            image: dict[str, Any],
        ) -> dict[str, Any]:
            async with semaphore:
                result = await self._search_one_image(
                    image["data"],
                    api_key,
                )

            return {
                "image_index": index,
                "filename": image["filename"],
                "image_source": image["image_source"],
                **result,
            }

        results = await asyncio.gather(
            *[
                search_reference(
                    index,
                    image,
                )
                for index, image in enumerate(
                    images,
                    start=1,
                )
            ]
        )

        found_count = sum(1 for result in results if result.get("found") is True)

        technical_error_count = sum(
            1 for result in results if result.get("technical_error") is True
        )

        output = {
            "image_count": len(images),
            "found_count": found_count,
            "technical_error_count": technical_error_count,
            "technical_error": (technical_error_count == len(images)),
            "results": results,
            "instruction": (
                "Use these results only as provenance evidence. "
                "Treat a specific source work as verified only when "
                "matching-image evidence consistently supports that work. "
                "Different textual variants may represent the same work "
                "when their semantic identity is clearly equivalent. "
                "A person name, character name, genre, or generic visual "
                "label alone is not sufficient work provenance. "
                "If results conflict, remain generic, or are weak, use "
                "unknown provenance. Do not guess. "
                "For style extraction, provenance must not alter the "
                "already-derived visual style profile."
            ),
        }

        return json.dumps(
            output,
            ensure_ascii=False,
            separators=(",", ":"),
        )
