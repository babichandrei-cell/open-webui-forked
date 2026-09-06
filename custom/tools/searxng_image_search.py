"""
title: SearXNG Image Search
description: Search images through a local SearXNG instance and pass selected search images into the vision model for visual inspection.
author: local
version: 0.1.0
"""

import asyncio
import base64
import json
import urllib.parse
import urllib.request

from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        searxng_base_url: str = Field(
            default="http://127.0.0.1:8888",
            description="Base URL of the local SearXNG instance.",
        )
        timeout_seconds: int = Field(
            default=15,
            description="Timeout for SearXNG and image downloads.",
        )
        max_results: int = Field(
            default=8,
            description="Maximum number of image search results returned.",
        )
        max_image_bytes: int = Field(
            default=6_000_000,
            description="Maximum downloaded image size in bytes.",
        )

    def __init__(self):
        self.valves = self.Valves()

    def _opener(self):
        # Deliberately ignore HTTP_PROXY / HTTPS_PROXY environment variables.
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _search_sync(self, query: str, count: int):
        params = urllib.parse.urlencode(
            {
                "q": query,
                "format": "json",
                "categories": "images",
                "language": "all",
                "safesearch": "0",
            }
        )

        url = self.valves.searxng_base_url.rstrip("/") + "/search?" + params

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "OpenWebUI-SearXNG-Image-Tool/0.1",
                "Accept": "application/json",
            },
        )

        with self._opener().open(
            req,
            timeout=self.valves.timeout_seconds,
        ) as response:
            data = json.loads(response.read().decode("utf-8"))

        results = data.get("results", [])

        return results[:count], data.get("unresponsive_engines", [])

    @staticmethod
    def _detect_image_mime(data: bytes, header_mime: str):
        mime = (header_mime or "").split(";", 1)[0].strip().lower()

        if mime.startswith("image/"):
            return mime

        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"

        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"

        if data.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"

        if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"

        return None

    def _download_image_sync(self, url: str):
        parsed = urllib.parse.urlparse(url)

        if parsed.scheme not in ("http", "https"):
            raise ValueError("Only HTTP and HTTPS image URLs are allowed.")

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/136.0 Safari/537.36"
                ),
                "Accept": (
                    "image/avif,image/webp,image/apng,"
                    "image/svg+xml,image/*,*/*;q=0.8"
                ),
            },
        )

        with self._opener().open(
            req,
            timeout=self.valves.timeout_seconds,
        ) as response:
            content_length = response.headers.get("Content-Length")

            if content_length:
                try:
                    if int(content_length) > self.valves.max_image_bytes:
                        raise ValueError("Image exceeds configured size limit.")
                except ValueError:
                    pass

            data = response.read(self.valves.max_image_bytes + 1)

            if len(data) > self.valves.max_image_bytes:
                raise ValueError("Image exceeds configured size limit.")

            mime = self._detect_image_mime(
                data,
                response.headers.get("Content-Type", ""),
            )

            if not mime:
                raise ValueError("Downloaded resource is not a supported image.")

            return data, mime

    async def search_images(
        self,
        query: str,
        count: int = 6,
    ) -> str:
        """
        Search the web for images using the local SearXNG image engines.
        Use this before inspect_search_image to discover candidate images.

        :param query: Image search query. For identification, prefer neutral visible features before candidate names.
        :param count: Number of image candidates to return, normally 4-8.
        :return: JSON containing candidate titles, source pages, image URLs, thumbnails, and engines.
        """

        count = max(
            1,
            min(int(count), int(self.valves.max_results)),
        )

        results, unresponsive = await asyncio.to_thread(
            self._search_sync,
            query,
            count,
        )

        candidates = []

        for index, result in enumerate(results, 1):
            candidates.append(
                {
                    "index": index,
                    "title": result.get("title", ""),
                    "page_url": result.get("url", ""),
                    "image_url": result.get("img_src", ""),
                    "thumbnail_url": (
                        result.get("thumbnail_src") or result.get("thumbnail") or ""
                    ),
                    "engines": result.get("engines", []),
                }
            )

        return json.dumps(
            {
                "query": query,
                "candidate_count": len(candidates),
                "unresponsive_engines": unresponsive,
                "candidates": candidates,
                "instruction": (
                    "Use inspect_search_image with the same query "
                    "and a candidate index to actually view an image. "
                    "Do not infer visual similarity from titles or URLs."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )

    async def inspect_search_image(
        self,
        query: str,
        index: int = 1,
    ) -> str:
        """
        Load one SearXNG image-search result and place the actual image into the model's vision context.
        Call this when visual comparison is required. Seeing a title or URL is not a substitute for calling this tool.

        :param query: The exact image-search query used to find the candidate.
        :param index: 1-based result index returned by search_images.
        :return: The selected image as vision input for the model.
        """

        index = int(index)

        if index < 1 or index > int(self.valves.max_results):
            raise ValueError(f"index must be between 1 and {self.valves.max_results}")

        results, _ = await asyncio.to_thread(
            self._search_sync,
            query,
            max(index, self.valves.max_results),
        )

        if index > len(results):
            raise ValueError(f"Search returned only {len(results)} results.")

        result = results[index - 1]

        image_url = result.get("img_src")
        thumbnail_url = result.get("thumbnail_src") or result.get("thumbnail")

        errors = []

        for candidate_url in (image_url, thumbnail_url):
            if not candidate_url:
                continue

            try:
                data, mime = await asyncio.to_thread(
                    self._download_image_sync,
                    candidate_url,
                )

                encoded = base64.b64encode(data).decode("ascii")

                # Open WebUI 0.11.1 detects a single data:image URI
                # returned by a Tool and inserts it into the next
                # model request as an image input.
                return f"data:{mime};base64,{encoded}"

            except Exception as exc:
                errors.append(str(exc))

        raise RuntimeError("Could not download candidate image. " + " | ".join(errors))
