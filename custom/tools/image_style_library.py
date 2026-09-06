"""
title: Image Style Library
author: Andrei
description: Saves structured image style profiles and their reference images to a persistent local style library.
required_open_webui_version: 0.11.0
version: 1.1.0
license: MIT
"""

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata

import aiohttp

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4


from open_webui.models.config import Config
from open_webui.models.users import Users
from open_webui.utils.terminals import (
    TERMINAL_CONTEXT_HEADER,
    get_terminal_server_url,
    terminal_context_config,
    terminal_context_id,
)
from open_webui.utils.tools import build_tool_server_headers, get_terminal_tools


class Tools:
    ROOT_DIR = Path("/srv/image_styles")
    DRAFT_ROOT = Path("/srv/image_style_drafts")

    SUPPORTED_MIME_TYPES = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }

    MAX_REFERENCES = 32
    MAX_REFERENCE_BYTES = 32 * 1024 * 1024
    MAX_TOTAL_BYTES = 128 * 1024 * 1024

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    # General helpers
    # ------------------------------------------------------------------

    def _now_iso(self) -> str:
        return (
            datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )

    def _clean_string(self, value: Any) -> str:
        if value is None:
            return ""

        if not isinstance(value, str):
            value = str(value)

        return value.strip()

    def _clean_string_list(self, values: Any) -> List[str]:
        if not isinstance(values, list):
            return []

        result: List[str] = []
        seen = set()

        for value in values:
            if not isinstance(value, str):
                continue

            value = value.strip()

            if not value:
                continue

            key = value.casefold()

            if key in seen:
                continue

            seen.add(key)
            result.append(value)

        return result

    # ------------------------------------------------------------------
    # Style naming
    # ------------------------------------------------------------------

    def _slugify(self, name: str) -> str:
        """
        Create a safe filesystem-friendly Unicode style ID.

        Unicode letters and numbers are preserved.
        Everything else becomes a single hyphen.
        """

        value = unicodedata.normalize("NFKC", name).strip().casefold()

        result: List[str] = []
        previous_dash = False

        for char in value:
            if char.isalnum():
                result.append(char)
                previous_dash = False
            else:
                if result and not previous_dash:
                    result.append("-")
                    previous_dash = True

        slug = "".join(result).strip("-")

        if not slug:
            slug = "style"

        slug = slug[:96].rstrip("-")

        return slug or "style"

    def _allocate_style_id(self, suggested_name: str) -> Tuple[str, Path]:
        base_id = self._slugify(suggested_name)

        candidate = base_id
        counter = 2

        while (self.ROOT_DIR / candidate).exists():
            candidate = f"{base_id}-{counter}"
            counter += 1

        return candidate, self.ROOT_DIR / candidate

    # ------------------------------------------------------------------
    # Image extraction
    # ------------------------------------------------------------------

    def _extract_url(self, image_url: Any) -> Optional[str]:
        if isinstance(image_url, str):
            return image_url

        if isinstance(image_url, dict):
            value = image_url.get("url")

            if isinstance(value, str):
                return value

        return None

    def _parse_image_data_url(
        self,
        value: str,
    ) -> Tuple[str, bytes]:
        """
        Decode one image data URL.

        Only embedded base64 image data is accepted.
        Remote URLs are deliberately not fetched.
        """

        match = re.match(
            r"^data:(image/[A-Za-z0-9.+_-]+);base64,(.+)$",
            value,
            flags=re.DOTALL,
        )

        if not match:
            raise ValueError(
                "Reference image is not an embedded base64 image data URL."
            )

        mime_type = match.group(1).lower()
        payload = match.group(2)

        if mime_type not in self.SUPPORTED_MIME_TYPES:
            raise ValueError(f"Unsupported reference image MIME type: {mime_type}")

        try:
            data = base64.b64decode(
                payload,
                validate=True,
            )
        except Exception as exc:
            raise ValueError(f"Invalid base64 reference image: {exc}") from exc

        if not data:
            raise ValueError("Reference image decoded to zero bytes.")

        if len(data) > self.MAX_REFERENCE_BYTES:
            raise ValueError(
                "Reference image exceeds the maximum allowed size "
                f"of {self.MAX_REFERENCE_BYTES} bytes."
            )

        self._validate_image_signature(
            mime_type=mime_type,
            data=data,
        )

        return mime_type, data

    def _validate_image_signature(
        self,
        mime_type: str,
        data: bytes,
    ) -> None:
        """
        Basic magic-byte validation.

        This avoids trusting the MIME declaration alone.
        """

        valid = False

        if mime_type in ("image/jpeg", "image/jpg"):
            valid = len(data) >= 3 and data[:3] == b"\xff\xd8\xff"

        elif mime_type == "image/png":
            valid = len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n"

        elif mime_type == "image/webp":
            valid = len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"

        if not valid:
            raise ValueError(
                f"Image bytes do not match declared MIME type {mime_type}."
            )

    def _get_latest_user_message(
        self,
        messages: List[dict],
    ) -> Optional[dict]:
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("role") == "user":
                return message

        return None

    def _extract_current_reference_images(
        self,
        messages: List[dict],
    ) -> List[Dict[str, Any]]:
        """
        Extract all images from the latest user message only.

        Older conversation images are intentionally ignored.
        """

        latest_user_message = self._get_latest_user_message(messages)

        if latest_user_message is None:
            raise ValueError("No user message is available in tool context.")

        content = latest_user_message.get("content")

        if not isinstance(content, list):
            raise ValueError("The latest user message contains no image attachments.")

        references: List[Dict[str, Any]] = []

        for part_index, part in enumerate(content):
            if not isinstance(part, dict):
                continue

            if part.get("type") != "image_url":
                continue

            url = self._extract_url(part.get("image_url"))

            if not url:
                raise ValueError(
                    f"Image attachment at content index "
                    f"{part_index} has no usable URL."
                )

            mime_type, data = self._parse_image_data_url(url)

            sha256 = hashlib.sha256(data).hexdigest()

            original_name = None

            for candidate in (
                part.get("name"),
                part.get("filename"),
            ):
                if isinstance(candidate, str) and candidate.strip():
                    original_name = Path(candidate).name
                    break

            image_url = part.get("image_url")

            if isinstance(image_url, dict):
                for candidate in (
                    image_url.get("name"),
                    image_url.get("filename"),
                ):
                    if (
                        original_name is None
                        and isinstance(candidate, str)
                        and candidate.strip()
                    ):
                        original_name = Path(candidate).name
                        break

            references.append(
                {
                    "mime_type": mime_type,
                    "extension": self.SUPPORTED_MIME_TYPES[mime_type],
                    "data": data,
                    "sha256": sha256,
                    "size_bytes": len(data),
                    "original_name": original_name,
                    "message_content_index": part_index,
                }
            )

        if not references:
            raise ValueError(
                "No reference images were found in the latest user message."
            )

        if len(references) > self.MAX_REFERENCES:
            raise ValueError(
                f"Too many reference images: {len(references)}. "
                f"Maximum is {self.MAX_REFERENCES}."
            )

        total_bytes = sum(item["size_bytes"] for item in references)

        if total_bytes > self.MAX_TOTAL_BYTES:
            raise ValueError(
                "Combined reference image size exceeds the maximum "
                f"allowed total of {self.MAX_TOTAL_BYTES} bytes."
            )

        return references

    def _deduplicate_references(
        self,
        references: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], int]:
        unique: List[Dict[str, Any]] = []
        seen_hashes = set()
        duplicates = 0

        for reference in references:
            digest = reference["sha256"]

            if digest in seen_hashes:
                duplicates += 1
                continue

            seen_hashes.add(digest)
            unique.append(reference)

        return unique, duplicates

    # ------------------------------------------------------------------
    # Files Workspace reference resolution
    # ------------------------------------------------------------------

    def _filesystem_attachment_path(
        self,
        item: Dict[str, Any],
    ) -> Optional[str]:
        if not isinstance(item, dict) or item.get("type") != "filesystem":
            return None

        nested = item.get("file")

        for candidate in (
            item.get("path"),
            nested.get("path") if isinstance(nested, dict) else None,
            item.get("id"),
            item.get("url"),
        ):
            if isinstance(candidate, str) and candidate.strip():
                value = candidate.strip()
                return value if value.startswith("/") else f"/{value}"

        return None

    def _filesystem_attachment_name(
        self,
        item: Dict[str, Any],
        source_path: str,
    ) -> str:
        nested = item.get("file")

        for candidate in (
            item.get("name"),
            item.get("filename"),
            nested.get("name") if isinstance(nested, dict) else None,
            nested.get("filename") if isinstance(nested, dict) else None,
        ):
            if isinstance(candidate, str) and candidate.strip():
                return Path(candidate).name

        return Path(source_path).name or "reference-image"

    async def _resolve_files_workspace(
        self,
        files: Any,
        request: Any,
        metadata: Dict[str, Any],
        user_context: Dict[str, Any],
        oauth_token: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[dict], Optional[dict], Optional[dict], Optional[str]]:
        if request is None:
            return None, None, None, None

        metadata = metadata if isinstance(metadata, dict) else {}

        terminal_id = metadata.get("terminal_id")
        if isinstance(terminal_id, str):
            terminal_id = terminal_id.strip() or None
        else:
            terminal_id = None

        attachment_terminal_ids = {
            str(item.get("terminal_id")).strip()
            for item in (files or [])
            if isinstance(item, dict)
            and item.get("type") == "filesystem"
            and isinstance(item.get("terminal_id"), str)
            and item.get("terminal_id").strip()
        }

        if terminal_id:
            resolved_terminal_id = terminal_id
        elif len(attachment_terminal_ids) == 1:
            resolved_terminal_id = next(iter(attachment_terminal_ids))
        else:
            return None, None, None, None

        connections = await Config.get(
            "terminal_server.connections",
            [],
        ) or []

        connection = next(
            (
                item
                for item in connections
                if isinstance(item, dict)
                and item.get("id") == resolved_terminal_id
            ),
            None,
        )

        if connection is None or not connection.get("enabled", True):
            return None, None, None, None

        config = connection.get("config") or {}

        if not (
            config.get("files_workspace") is True
            or config.get("chat_uploads") == "filesystem"
        ):
            return None, None, None, None

        user_id = None
        if isinstance(user_context, dict) and user_context.get("id"):
            user_id = str(user_context["id"])

        if not user_id:
            return None, None, None, None

        user = await Users.get_user_by_id(user_id)
        if user is None:
            return None, None, None, None

        await get_terminal_tools(
            request,
            resolved_terminal_id,
            user,
            {
                "__metadata__": metadata,
                "__oauth_token__": oauth_token,
            },
        )

        headers, cookies = await build_tool_server_headers(
            connection,
            request,
            user,
            server_id=resolved_terminal_id,
            metadata=metadata,
            extra_params={
                "__oauth_token__": oauth_token,
            },
        )

        headers["X-User-Id"] = user.id

        chat_id = metadata.get("chat_id")
        if chat_id:
            headers["X-Session-Id"] = str(chat_id)

        terminal_context = (
            "automation"
            if metadata.get("automation_id")
            else "chat"
        )

        context_id = terminal_context_id(
            connection,
            metadata,
            terminal_context,
        )

        context_config = terminal_context_config(
            connection,
            terminal_context,
        )

        if (
            isinstance(context_config, dict)
            and context_config.get("context_id")
            in {"chat_id", "automation_id"}
            and not context_id
        ):
            return None, None, None, None

        if context_id:
            headers[TERMINAL_CONTEXT_HEADER] = context_id

        return connection, headers, cookies, resolved_terminal_id

    async def _extract_filesystem_reference_images(
        self,
        files: Any,
        request: Any,
        metadata: Dict[str, Any],
        user_context: Dict[str, Any],
        oauth_token: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        attachments = [
            item
            for item in (files or [])
            if isinstance(item, dict)
            and item.get("type") == "filesystem"
        ]

        if not attachments:
            return []

        connection, headers, cookies, terminal_id = (
            await self._resolve_files_workspace(
                files,
                request,
                metadata,
                user_context,
                oauth_token,
            )
        )

        if (
            connection is None
            or headers is None
            or cookies is None
            or not terminal_id
        ):
            raise ValueError(
                "Files Workspace reference images could not be resolved "
                "through the authorized workspace connection."
            )

        base_url = get_terminal_server_url(connection).rstrip("/")
        timeout = aiohttp.ClientTimeout(total=45)

        references: List[Dict[str, Any]] = []
        total_bytes = 0

        async with aiohttp.ClientSession(
            timeout=timeout,
            trust_env=True,
        ) as session:
            for item in attachments:
                item_terminal_id = item.get("terminal_id")
                if (
                    isinstance(item_terminal_id, str)
                    and item_terminal_id.strip()
                    and item_terminal_id.strip() != terminal_id
                ):
                    continue

                source_path = self._filesystem_attachment_path(item)
                if not source_path:
                    continue

                original_name = self._filesystem_attachment_name(
                    item,
                    source_path,
                )

                try:
                    async with session.get(
                        f"{base_url}/files/view",
                        params={"path": source_path},
                        headers=headers,
                        cookies=cookies,
                    ) as response:
                        if response.status != 200:
                            raise ValueError(
                                f"Could not read Files Workspace reference "
                                f"{original_name!r}: HTTP {response.status}."
                            )

                        chunks: List[bytes] = []
                        size = 0

                        async for chunk in response.content.iter_chunked(
                            64 * 1024
                        ):
                            if not chunk:
                                continue

                            size += len(chunk)

                            if size > self.MAX_REFERENCE_BYTES:
                                raise ValueError(
                                    f"Reference image {original_name!r} "
                                    f"exceeds {self.MAX_REFERENCE_BYTES} bytes."
                                )

                            chunks.append(chunk)

                        data = b"".join(chunks)

                        declared_mime = (
                            response.headers.get("Content-Type", "")
                            .split(";", 1)[0]
                            .strip()
                            .lower()
                        )

                except ValueError:
                    raise
                except Exception as exc:
                    raise ValueError(
                        f"Could not read Files Workspace reference "
                        f"{original_name!r}: {exc}"
                    ) from exc

                if not data:
                    raise ValueError(
                        f"Reference image {original_name!r} decoded to zero bytes."
                    )

                detected_mime = None

                if data[:3] == b"\xff\xd8\xff":
                    detected_mime = "image/jpeg"
                elif data[:8] == b"\x89PNG\r\n\x1a\n":
                    detected_mime = "image/png"
                elif (
                    len(data) >= 12
                    and data[:4] == b"RIFF"
                    and data[8:12] == b"WEBP"
                ):
                    detected_mime = "image/webp"

                if detected_mime not in self.SUPPORTED_MIME_TYPES:
                    continue

                if (
                    declared_mime.startswith("image/")
                    and declared_mime in self.SUPPORTED_MIME_TYPES
                    and declared_mime != detected_mime
                    and not (
                        declared_mime == "image/jpg"
                        and detected_mime == "image/jpeg"
                    )
                ):
                    raise ValueError(
                        f"Reference image {original_name!r} MIME does not "
                        "match its content signature."
                    )

                total_bytes += len(data)

                if total_bytes > self.MAX_TOTAL_BYTES:
                    raise ValueError(
                        "Combined reference image size exceeds the maximum "
                        f"allowed total of {self.MAX_TOTAL_BYTES} bytes."
                    )

                references.append(
                    {
                        "mime_type": detected_mime,
                        "extension": self.SUPPORTED_MIME_TYPES[detected_mime],
                        "data": data,
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "size_bytes": len(data),
                        "original_name": original_name,
                        "source_path": source_path,
                        "source": "files_workspace",
                        "terminal_id": terminal_id,
                    }
                )

        return references

    async def _collect_reference_images(
        self,
        messages: List[dict],
        files: Any,
        request: Any,
        metadata: Dict[str, Any],
        user_context: Dict[str, Any],
        oauth_token: Optional[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], int]:
        references: List[Dict[str, Any]] = []

        try:
            references.extend(
                self._extract_current_reference_images(messages)
            )
        except ValueError:
            pass

        references.extend(
            await self._extract_filesystem_reference_images(
                files,
                request,
                metadata,
                user_context,
                oauth_token,
            )
        )

        if not references:
            raise ValueError(
                "No supported reference images were found in the current user turn."
            )

        references, duplicates_skipped = self._deduplicate_references(
            references
        )

        if len(references) > self.MAX_REFERENCES:
            raise ValueError(
                f"Too many reference images: {len(references)}. "
                f"Maximum is {self.MAX_REFERENCES}."
            )

        total_bytes = sum(
            reference["size_bytes"]
            for reference in references
        )

        if total_bytes > self.MAX_TOTAL_BYTES:
            raise ValueError(
                "Combined reference image size exceeds the maximum "
                f"allowed total of {self.MAX_TOTAL_BYTES} bytes."
            )

        return references, duplicates_skipped

    # ------------------------------------------------------------------
    # Draft helpers
    # ------------------------------------------------------------------

    def _ensure_draft_root(self) -> None:
        self.DRAFT_ROOT.mkdir(
            parents=True,
            exist_ok=True,
        )

        if not os.access(self.DRAFT_ROOT, os.W_OK):
            raise PermissionError(
                f"Open WebUI cannot write to {self.DRAFT_ROOT}"
            )

    def _draft_path(self, draft_id: str) -> Path:
        draft_id = self._clean_string(draft_id)

        if not re.fullmatch(r"draft-[0-9a-f]{32}", draft_id):
            raise ValueError("Invalid image style draft_id.")

        path = (self.DRAFT_ROOT / draft_id).resolve()
        root = self.DRAFT_ROOT.resolve()

        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("Invalid image style draft path.") from exc

        return path

    def _load_draft(self, draft_id: str) -> Tuple[Path, Dict[str, Any]]:
        path = self._draft_path(draft_id)
        draft_file = path / "draft.json"

        if not draft_file.is_file():
            raise ValueError(f"Image style draft not found: {draft_id}")

        with open(
            draft_file,
            "r",
            encoding="utf-8",
        ) as handle:
            document = json.load(handle)

        if not isinstance(document, dict):
            raise ValueError("Image style draft document is invalid.")

        if document.get("draft_id") != draft_id:
            raise ValueError("Image style draft identity mismatch.")

        return path, document

    def _style_profile(
        self,
        style_identity: str,
        medium: str,
        lighting: str,
        color_palette: str,
        tonal_response: str,
        texture: str,
        optics: str,
        depth_of_field: str,
        composition: str,
        atmosphere: str,
        style_tags: List[str],
        style_keywords: List[str],
        generation_guidance: str,
        avoid: str,
    ) -> Dict[str, Any]:
        return {
            "style_identity": self._clean_string(style_identity),
            "medium": self._clean_string(medium),
            "lighting": self._clean_string(lighting),
            "color_palette": self._clean_string(color_palette),
            "tonal_response": self._clean_string(tonal_response),
            "texture": self._clean_string(texture),
            "optics": self._clean_string(optics),
            "depth_of_field": self._clean_string(depth_of_field),
            "composition": self._clean_string(composition),
            "atmosphere": self._clean_string(atmosphere),
            "style_tags": self._clean_string_list(style_tags),
            "style_keywords": self._clean_string_list(style_keywords),
            "generation_guidance": self._clean_string(
                generation_guidance
            ),
            "avoid": self._clean_string(avoid),
        }

    def _provenance(
        self,
        provenance_status: str,
        source_type: str,
        source_title: str,
        creator: str,
    ) -> Dict[str, Any]:
        status = (
            self._clean_string(provenance_status).lower()
            or "unknown"
        )

        allowed = {
            "unknown",
            "user_provided",
            "verified",
        }

        if status not in allowed:
            raise ValueError(
                "provenance_status must be one of: "
                "unknown, user_provided, verified."
            )

        source_type = self._clean_string(source_type)
        source_title = self._clean_string(source_title)
        creator = self._clean_string(creator)

        if status == "unknown":
            source_type = ""
            source_title = ""
            creator = ""

        return {
            "status": status,
            "source_type": source_type or None,
            "title": source_title or None,
            "creator": creator or None,
        }

    def _draft_report(
        self,
        document: Dict[str, Any],
        status: str,
    ) -> str:
        return json.dumps(
            {
                "status": status,
                "draft_id": document.get("draft_id"),
                "name": document.get("name"),
                "reference_count": document.get(
                    "reference_count",
                    0,
                ),
                "duplicates_skipped": document.get(
                    "duplicates_skipped",
                    0,
                ),
                "provenance": document.get("provenance", {}),
                "style": document.get("style", {}),
            },
            ensure_ascii=False,
        )

    # ------------------------------------------------------------------
    # Filesystem helpers
    # ------------------------------------------------------------------

    def _ensure_root(self) -> None:
        self.ROOT_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        if not os.access(self.ROOT_DIR, os.W_OK):
            raise PermissionError(f"Open WebUI cannot write to {self.ROOT_DIR}")

    def _write_bytes(
        self,
        path: Path,
        data: bytes,
    ) -> None:
        with open(path, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())

    def _write_json(
        self,
        path: Path,
        data: Dict[str, Any],
    ) -> None:
        encoded = (
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")

        self._write_bytes(path, encoded)

    # ------------------------------------------------------------------
    # Library reading
    # ------------------------------------------------------------------

    MAX_LIST_RESULTS = 100

    def _normalize_lookup(
        self,
        value: Any,
    ) -> str:
        value = self._clean_string(value)

        if not value:
            return ""

        value = unicodedata.normalize(
            "NFKC",
            value,
        ).casefold()

        value = re.sub(
            r"\s+",
            " ",
            value,
        ).strip()

        return value

    def _load_style_documents(
        self,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Load valid style.json documents from the library.

        Hidden / temporary directories are ignored.

        Returns:
            (valid_records, invalid_records)
        """

        self._ensure_root()

        records: List[Dict[str, Any]] = []
        invalid: List[Dict[str, Any]] = []

        try:
            directories = sorted(
                self.ROOT_DIR.iterdir(),
                key=lambda path: path.name.casefold(),
            )
        except Exception as exc:
            raise RuntimeError(f"Could not read image style library: {exc}") from exc

        for directory in directories:
            if not directory.is_dir():
                continue

            if directory.name.startswith("."):
                continue

            style_path = directory / "style.json"

            if not style_path.is_file():
                continue

            try:
                with open(
                    style_path,
                    "r",
                    encoding="utf-8",
                ) as handle:
                    document = json.load(handle)

                if not isinstance(document, dict):
                    raise ValueError("style.json root must be an object.")

                style_id = document.get("style_id")
                name = document.get("name")
                style = document.get("style")

                if not isinstance(style_id, str) or not style_id.strip():
                    raise ValueError("style_id is missing or invalid.")

                if not isinstance(name, str) or not name.strip():
                    raise ValueError("name is missing or invalid.")

                if not isinstance(style, dict):
                    raise ValueError("style object is missing or invalid.")

                records.append(
                    {
                        "directory": directory,
                        "style_path": style_path,
                        "document": document,
                    }
                )

            except Exception as exc:
                invalid.append(
                    {
                        "directory": directory.name,
                        "error": str(exc),
                    }
                )

        return records, invalid

    def _catalog_entry(
        self,
        document: Dict[str, Any],
    ) -> Dict[str, Any]:
        provenance = document.get("provenance")

        if not isinstance(provenance, dict):
            provenance = {}

        style = document.get("style")

        if not isinstance(style, dict):
            style = {}

        tags = style.get("style_tags")

        if not isinstance(tags, list):
            tags = []

        return {
            "style_id": document.get("style_id"),
            "name": document.get("name"),
            "created_at": document.get("created_at"),
            "reference_count": document.get(
                "reference_count",
                0,
            ),
            "provenance": {
                "status": provenance.get(
                    "status",
                    "unknown",
                ),
                "source_type": provenance.get("source_type"),
                "title": provenance.get("title"),
                "creator": provenance.get("creator"),
            },
            "style_identity": style.get(
                "style_identity",
                "",
            ),
            "style_tags": tags,
        }

    def _record_search_text(
        self,
        document: Dict[str, Any],
    ) -> str:
        """
        Build normalized searchable text for catalog filtering.
        """

        provenance = document.get("provenance")

        if not isinstance(provenance, dict):
            provenance = {}

        style = document.get("style")

        if not isinstance(style, dict):
            style = {}

        values: List[str] = [
            self._clean_string(document.get("style_id")),
            self._clean_string(document.get("name")),
            self._clean_string(provenance.get("source_type")),
            self._clean_string(provenance.get("title")),
            self._clean_string(provenance.get("creator")),
            self._clean_string(style.get("style_identity")),
            self._clean_string(style.get("medium")),
            self._clean_string(style.get("atmosphere")),
        ]

        for field in (
            "style_tags",
            "style_keywords",
        ):
            items = style.get(field)

            if isinstance(items, list):
                values.extend(
                    self._clean_string(item) for item in items if isinstance(item, str)
                )

        return self._normalize_lookup(" ".join(value for value in values if value))

    def _resolve_style_record(
        self,
        query: str,
        records: List[Dict[str, Any]],
    ) -> Tuple[
        Optional[Dict[str, Any]],
        List[Dict[str, Any]],
    ]:
        """
        Resolve a style by:

        1. exact style_id
        2. exact library name
        3. exact provenance title
        4. unique partial match

        Returns:
            (resolved_record, ambiguous_candidates)
        """

        normalized_query = self._normalize_lookup(query)

        if not normalized_query:
            return None, []

        exact_id: List[Dict[str, Any]] = []
        exact_name: List[Dict[str, Any]] = []
        exact_title: List[Dict[str, Any]] = []
        partial: List[Dict[str, Any]] = []

        for record in records:
            document = record["document"]

            provenance = document.get("provenance")

            if not isinstance(provenance, dict):
                provenance = {}

            style_id = self._normalize_lookup(document.get("style_id"))
            name = self._normalize_lookup(document.get("name"))
            title = self._normalize_lookup(provenance.get("title"))

            if normalized_query == style_id:
                exact_id.append(record)
                continue

            if normalized_query == name:
                exact_name.append(record)
                continue

            if title and normalized_query == title:
                exact_title.append(record)
                continue

            search_text = self._record_search_text(document)

            if normalized_query in search_text:
                partial.append(record)

        if len(exact_id) == 1:
            return exact_id[0], []

        if len(exact_name) == 1:
            return exact_name[0], []

        if len(exact_name) > 1:
            return None, exact_name

        if len(exact_title) == 1:
            return exact_title[0], []

        if len(exact_title) > 1:
            return None, exact_title

        if len(partial) == 1:
            return partial[0], []

        if len(partial) > 1:
            return None, partial

        return None, []

    async def create_image_style_draft(
        self,
        suggested_name: str,
        style_identity: str,
        medium: str,
        lighting: str,
        color_palette: str,
        tonal_response: str,
        texture: str,
        optics: str,
        depth_of_field: str,
        composition: str,
        atmosphere: str,
        style_tags: List[str],
        style_keywords: List[str],
        generation_guidance: str,
        avoid: str,
        provenance_status: str = "unknown",
        source_type: str = "",
        source_title: str = "",
        creator: str = "",
        __messages__: List[dict] = [],
        __files__: List[dict] = [],
        __metadata__: Dict[str, Any] = {},
        __request__: Any = None,
        __user__: Dict[str, Any] = {},
        __oauth_token__: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Create a non-published image-style draft.

        This snapshots all current reference-image bytes immediately so a
        later approval turn does not depend on historical chat attachments
        or the continued availability of a Files Workspace source.

        Creating a draft does NOT publish a style into /srv/image_styles.
        """

        name = self._clean_string(suggested_name)

        if not name:
            raise ValueError("suggested_name must not be empty.")

        if len(name) > 160:
            raise ValueError("suggested_name is too long.")

        references, duplicates_skipped = (
            await self._collect_reference_images(
                __messages__,
                __files__,
                __request__,
                __metadata__,
                __user__,
                __oauth_token__,
            )
        )

        self._ensure_draft_root()

        draft_id = f"draft-{uuid4().hex}"

        tmp_dir = Path(
            tempfile.mkdtemp(
                prefix=f".tmp-{draft_id}-",
                dir=self.DRAFT_ROOT,
            )
        )

        final_dir = self.DRAFT_ROOT / draft_id
        references_dir = tmp_dir / "references"

        try:
            references_dir.mkdir(
                parents=True,
                exist_ok=False,
            )

            reference_metadata: List[Dict[str, Any]] = []

            for index, reference in enumerate(
                references,
                start=1,
            ):
                extension = reference["extension"]
                filename = f"ref_{index:03d}.{extension}"
                relative_path = Path("references") / filename
                absolute_path = tmp_dir / relative_path

                self._write_bytes(
                    absolute_path,
                    reference["data"],
                )

                item: Dict[str, Any] = {
                    "filename": relative_path.as_posix(),
                    "mime_type": reference["mime_type"],
                    "size_bytes": reference["size_bytes"],
                    "sha256": reference["sha256"],
                    "source": reference.get(
                        "source",
                        "open_webui_message_data_url",
                    ),
                }

                if reference.get("original_name"):
                    item["original_name"] = reference[
                        "original_name"
                    ]

                if reference.get("source_path"):
                    item["source_path"] = reference[
                        "source_path"
                    ]

                reference_metadata.append(item)

            now = self._now_iso()

            document: Dict[str, Any] = {
                "schema_version": 1,
                "artifact_type": "image_style_draft",
                "draft_id": draft_id,
                "name": name,
                "created_at": now,
                "updated_at": now,
                "reference_count": len(reference_metadata),
                "duplicates_skipped": duplicates_skipped,
                "references": reference_metadata,
                "provenance": self._provenance(
                    provenance_status,
                    source_type,
                    source_title,
                    creator,
                ),
                "style": self._style_profile(
                    style_identity,
                    medium,
                    lighting,
                    color_palette,
                    tonal_response,
                    texture,
                    optics,
                    depth_of_field,
                    composition,
                    atmosphere,
                    style_tags,
                    style_keywords,
                    generation_guidance,
                    avoid,
                ),
            }

            self._write_json(
                tmp_dir / "draft.json",
                document,
            )

            if final_dir.exists():
                raise FileExistsError(
                    f"Draft destination unexpectedly exists: "
                    f"{final_dir}"
                )

            os.rename(
                tmp_dir,
                final_dir,
            )

            tmp_dir = None

        except Exception:
            if tmp_dir is not None and tmp_dir.exists():
                shutil.rmtree(
                    tmp_dir,
                    ignore_errors=True,
                )
            raise

        return self._draft_report(
            document,
            "draft_created",
        )

    async def update_image_style_draft(
        self,
        draft_id: str,
        suggested_name: str,
        style_identity: str,
        medium: str,
        lighting: str,
        color_palette: str,
        tonal_response: str,
        texture: str,
        optics: str,
        depth_of_field: str,
        composition: str,
        atmosphere: str,
        style_tags: List[str],
        style_keywords: List[str],
        generation_guidance: str,
        avoid: str,
        provenance_status: str = "unknown",
        source_type: str = "",
        source_title: str = "",
        creator: str = "",
    ) -> str:
        """
        Replace the editable profile/provenance/name of an existing draft.

        Snapshotted reference images are preserved unchanged.
        This does not publish the draft.
        """

        draft_dir, document = self._load_draft(
            self._clean_string(draft_id)
        )

        name = self._clean_string(suggested_name)

        if not name:
            raise ValueError("suggested_name must not be empty.")

        if len(name) > 160:
            raise ValueError("suggested_name is too long.")

        document["name"] = name
        document["updated_at"] = self._now_iso()
        document["provenance"] = self._provenance(
            provenance_status,
            source_type,
            source_title,
            creator,
        )
        document["style"] = self._style_profile(
            style_identity,
            medium,
            lighting,
            color_palette,
            tonal_response,
            texture,
            optics,
            depth_of_field,
            composition,
            atmosphere,
            style_tags,
            style_keywords,
            generation_guidance,
            avoid,
        )

        temp_file = draft_dir / ".draft.json.tmp"

        try:
            self._write_json(
                temp_file,
                document,
            )
            os.replace(
                temp_file,
                draft_dir / "draft.json",
            )
        finally:
            if temp_file.exists():
                temp_file.unlink(
                    missing_ok=True
                )

        return self._draft_report(
            document,
            "draft_updated",
        )

    async def publish_image_style_draft(
        self,
        draft_id: str,
    ) -> str:
        """
        Publish one approved draft into the global Image Style Library.

        The operation does not read current chat attachments. It publishes
        only the exact profile and reference bytes already snapshotted in
        the draft.
        """

        self._ensure_root()
        self._ensure_draft_root()

        draft_dir, draft = self._load_draft(
            self._clean_string(draft_id)
        )

        name = self._clean_string(
            draft.get("name")
        )

        if not name:
            raise ValueError("Draft has no valid style name.")

        references = draft.get("references")
        if not isinstance(references, list) or not references:
            raise ValueError(
                "Draft contains no reference images."
            )

        style = draft.get("style")
        provenance = draft.get("provenance")

        if not isinstance(style, dict):
            raise ValueError(
                "Draft style profile is invalid."
            )

        if not isinstance(provenance, dict):
            provenance = {
                "status": "unknown",
                "source_type": None,
                "title": None,
                "creator": None,
            }

        style_id, final_dir = self._allocate_style_id(
            name
        )

        tmp_dir = Path(
            tempfile.mkdtemp(
                prefix=f".tmp-{style_id}-",
                dir=self.ROOT_DIR,
            )
        )

        try:
            (tmp_dir / "references").mkdir(
                parents=True,
                exist_ok=False,
            )

            published_references: List[Dict[str, Any]] = []

            for item in references:
                if not isinstance(item, dict):
                    raise ValueError(
                        "Draft reference metadata is invalid."
                    )

                filename = item.get("filename")
                expected_sha = item.get("sha256")

                if (
                    not isinstance(filename, str)
                    or not filename
                    or not isinstance(expected_sha, str)
                    or not re.fullmatch(
                        r"[0-9a-f]{64}",
                        expected_sha,
                    )
                ):
                    raise ValueError(
                        "Draft reference metadata is invalid."
                    )

                source = (
                    draft_dir / filename
                ).resolve()

                try:
                    source.relative_to(
                        draft_dir.resolve()
                    )
                except ValueError as exc:
                    raise ValueError(
                        "Draft reference path escapes draft root."
                    ) from exc

                if not source.is_file():
                    raise ValueError(
                        f"Draft reference is missing: {filename}"
                    )

                data = source.read_bytes()
                actual_sha = hashlib.sha256(
                    data
                ).hexdigest()

                if actual_sha != expected_sha:
                    raise ValueError(
                        f"Draft reference checksum mismatch: "
                        f"{filename}"
                    )

                destination = (
                    tmp_dir / filename
                ).resolve()

                try:
                    destination.relative_to(
                        tmp_dir.resolve()
                    )
                except ValueError as exc:
                    raise ValueError(
                        "Published reference path escapes style root."
                    ) from exc

                destination.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                self._write_bytes(
                    destination,
                    data,
                )

                published_item = dict(item)
                published_item.pop(
                    "source_path",
                    None,
                )
                published_references.append(
                    published_item
                )

            created_at = self._now_iso()

            document: Dict[str, Any] = {
                "schema_version": 1,
                "style_id": style_id,
                "name": name,
                "created_at": created_at,
                "reference_count": len(
                    published_references
                ),
                "references": published_references,
                "provenance": provenance,
                "style": style,
            }

            self._write_json(
                tmp_dir / "style.json",
                document,
            )

            if final_dir.exists():
                raise FileExistsError(
                    f"Style destination unexpectedly exists: "
                    f"{final_dir}"
                )

            os.rename(
                tmp_dir,
                final_dir,
            )

            tmp_dir = None

        except Exception:
            if tmp_dir is not None and tmp_dir.exists():
                shutil.rmtree(
                    tmp_dir,
                    ignore_errors=True,
                )
            raise

        shutil.rmtree(
            draft_dir,
            ignore_errors=False,
        )

        return json.dumps(
            {
                "status": "saved",
                "draft_id": draft_id,
                "style_id": style_id,
                "name": name,
                "path": str(final_dir),
                "reference_count": len(
                    published_references
                ),
                "duplicates_skipped": draft.get(
                    "duplicates_skipped",
                    0,
                ),
            },
            ensure_ascii=False,
        )

    async def discard_image_style_draft(
        self,
        draft_id: str,
    ) -> str:
        """
        Permanently discard one non-published image-style draft.
        """

        draft_dir, document = self._load_draft(
            self._clean_string(draft_id)
        )

        shutil.rmtree(
            draft_dir,
            ignore_errors=False,
        )

        return json.dumps(
            {
                "status": "draft_discarded",
                "draft_id": document.get(
                    "draft_id"
                ),
                "name": document.get("name"),
            },
            ensure_ascii=False,
        )

    async def list_image_styles(
        self,
        query: str = "",
        limit: int = 50,
    ) -> str:
        """
        List visual styles stored in the local image-style library.

        Use this when the user asks what visual styles are available,
        wants to browse saved styles, or wants to find styles matching
        a name, source work, tag, keyword, or visual description.

        `query` is optional. When present, only matching styles are returned.

        This function returns compact catalog entries rather than complete
        style profiles.

        When the user asks to list, show, browse, search, or discover available
        styles, use this function alone.

        Do NOT call `get_image_style` merely to enrich a listing.

        Call `get_image_style` only when:
        - the user explicitly asks for the full profile or details of a specific style;
        - the user selects a specific style from the catalog;
        - a downstream workflow such as image generation requires the complete profile.
        """

        records, invalid = self._load_style_documents()

        try:
            limit = int(limit)
        except Exception:
            limit = 50

        limit = max(
            1,
            min(
                limit,
                self.MAX_LIST_RESULTS,
            ),
        )

        normalized_query = self._normalize_lookup(query)
        query_tokens = [token for token in normalized_query.split() if token]
        matches: List[Dict[str, Any]] = []

        for record in records:
            document = record["document"]

            if query_tokens:
                search_text = self._record_search_text(document)

                if not all(token in search_text for token in query_tokens):
                    continue

            matches.append(self._catalog_entry(document))

        matches.sort(
            key=lambda item: (
                self._normalize_lookup(item.get("name")),
                self._normalize_lookup(item.get("style_id")),
            )
        )

        total_matches = len(matches)
        matches = matches[:limit]

        result = {
            "status": "ok",
            "query": self._clean_string(query),
            "library_count": len(records),
            "match_count": total_matches,
            "returned_count": len(matches),
            "styles": matches,
            "invalid_entries": len(invalid),
        }

        return json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    async def get_image_style(
        self,
        style: str,
    ) -> str:
        """
        Retrieve one complete visual style from the local image-style library.

        Use this only when a specific style has been selected or when the complete
        profile is required for a downstream operation.

        Do not call this function automatically after `list_image_styles` merely
        to provide additional detail in a catalog response.

        `style` may be:

        - a style_id;
        - an exact library name;
        - an exact provenance title;
        - a unique partial name or search match.

        Use this before applying a saved visual style to image generation.

        Never invent a style profile when no matching saved style exists.
        If several styles match, return the candidates and let the user or
        calling model disambiguate.
        """

        query = self._clean_string(style)

        if not query:
            return json.dumps(
                {
                    "status": "error",
                    "reason": ("A style name or style_id is required."),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )

        records, invalid = self._load_style_documents()

        record, ambiguous = self._resolve_style_record(
            query,
            records,
        )

        if record is None:
            if ambiguous:
                candidates = [
                    self._catalog_entry(item["document"]) for item in ambiguous[:20]
                ]

                return json.dumps(
                    {
                        "status": "ambiguous",
                        "query": query,
                        "candidate_count": len(ambiguous),
                        "candidates": candidates,
                        "instruction": (
                            "Several saved styles match this query. "
                            "Choose one specific style_id."
                        ),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )

            return json.dumps(
                {
                    "status": "not_found",
                    "query": query,
                    "library_count": len(records),
                    "invalid_entries": len(invalid),
                    "instruction": (
                        "Do not invent a style. "
                        "Use list_image_styles to inspect "
                        "available styles."
                    ),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )

        document = record["document"]
        directory = record["directory"]
        references = document.get("references")
        reference_files: List[str] = []

        if isinstance(references, list):
            for item in references:
                if not isinstance(item, dict):
                    continue

                filename = item.get("filename")

                if not isinstance(filename, str):
                    continue

                candidate = (directory / filename).resolve()

                try:
                    candidate.relative_to(directory.resolve())
                except ValueError:
                    continue

                if candidate.is_file():
                    reference_files.append(str(candidate))

        result = {
            "status": "found",
            "style_id": document.get("style_id"),
            "name": document.get("name"),
            "created_at": document.get("created_at"),
            "reference_count": document.get(
                "reference_count",
                len(reference_files),
            ),
            "reference_files": reference_files,
            "provenance": document.get(
                "provenance",
                {
                    "status": "unknown",
                    "source_type": None,
                    "title": None,
                    "creator": None,
                },
            ),
            "style": document.get(
                "style",
                {},
            ),
        }

        return json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    async def save_image_style(
        self,
        suggested_name: str,
        style_identity: str,
        medium: str,
        lighting: str,
        color_palette: str,
        tonal_response: str,
        texture: str,
        optics: str,
        depth_of_field: str,
        composition: str,
        atmosphere: str,
        style_tags: List[str],
        style_keywords: List[str],
        generation_guidance: str,
        avoid: str,
        provenance_status: str = "unknown",
        source_type: str = "",
        source_title: str = "",
        creator: str = "",
        __messages__: List[dict] = [],
    ) -> str:
        """
        Save one extracted visual style and all reference images attached
        to the latest user message.

        Multiple attached images are stored as references for the same
        style.

        provenance_status must be one of:
        - unknown
        - user_provided
        - verified

        Provenance must never be guessed from visual appearance.
        """

        name = self._clean_string(suggested_name)

        if not name:
            raise ValueError("suggested_name must not be empty.")

        if len(name) > 160:
            raise ValueError("suggested_name is too long.")

        provenance_status = self._clean_string(provenance_status).lower() or "unknown"

        allowed_provenance = {
            "unknown",
            "user_provided",
            "verified",
        }

        if provenance_status not in allowed_provenance:
            raise ValueError(
                "provenance_status must be one of: " "unknown, user_provided, verified."
            )

        source_type = self._clean_string(source_type)
        source_title = self._clean_string(source_title)
        creator = self._clean_string(creator)

        if provenance_status == "unknown":
            source_type = ""
            source_title = ""
            creator = ""

        self._ensure_root()
        references = self._extract_current_reference_images(__messages__)
        references, duplicates_skipped = self._deduplicate_references(references)

        if not references:
            raise ValueError("No unique reference images remain after deduplication.")

        style_id, final_dir = self._allocate_style_id(name)

        tmp_dir = Path(
            tempfile.mkdtemp(
                prefix=f".tmp-{style_id}-",
                dir=self.ROOT_DIR,
            )
        )

        references_dir = tmp_dir / "references"

        try:
            references_dir.mkdir(
                parents=True,
                exist_ok=False,
            )

            reference_metadata: List[Dict[str, Any]] = []

            for index, reference in enumerate(
                references,
                start=1,
            ):
                extension = reference["extension"]
                filename = f"ref_{index:03d}.{extension}"
                relative_path = Path("references") / filename
                absolute_path = tmp_dir / relative_path

                self._write_bytes(
                    absolute_path,
                    reference["data"],
                )

                item = {
                    "filename": relative_path.as_posix(),
                    "mime_type": reference["mime_type"],
                    "size_bytes": reference["size_bytes"],
                    "sha256": reference["sha256"],
                    "source": "open_webui_message_data_url",
                }

                if reference["original_name"]:
                    item["original_name"] = reference["original_name"]

                reference_metadata.append(item)

            style_profile = {
                "style_identity": self._clean_string(style_identity),
                "medium": self._clean_string(medium),
                "lighting": self._clean_string(lighting),
                "color_palette": self._clean_string(color_palette),
                "tonal_response": self._clean_string(tonal_response),
                "texture": self._clean_string(texture),
                "optics": self._clean_string(optics),
                "depth_of_field": self._clean_string(depth_of_field),
                "composition": self._clean_string(composition),
                "atmosphere": self._clean_string(atmosphere),
                "style_tags": self._clean_string_list(style_tags),
                "style_keywords": self._clean_string_list(style_keywords),
                "generation_guidance": self._clean_string(generation_guidance),
                "avoid": self._clean_string(avoid),
            }

            document: Dict[str, Any] = {
                "schema_version": 1,
                "style_id": style_id,
                "name": name,
                "created_at": self._now_iso(),
                "reference_count": len(reference_metadata),
                "references": reference_metadata,
                "provenance": {
                    "status": provenance_status,
                    "source_type": source_type or None,
                    "title": source_title or None,
                    "creator": creator or None,
                },
                "style": style_profile,
            }

            self._write_json(
                tmp_dir / "style.json",
                document,
            )

            if final_dir.exists():
                raise FileExistsError(
                    f"Style destination unexpectedly exists: " f"{final_dir}"
                )

            os.rename(
                tmp_dir,
                final_dir,
            )

            tmp_dir = None

        except Exception:
            if tmp_dir is not None and tmp_dir.exists():
                shutil.rmtree(
                    tmp_dir,
                    ignore_errors=True,
                )

            raise

        result = {
            "status": "saved",
            "style_id": style_id,
            "name": name,
            "path": str(final_dir),
            "reference_count": len(reference_metadata),
            "duplicates_skipped": duplicates_skipped,
        }

        return json.dumps(
            result,
            ensure_ascii=False,
        )
