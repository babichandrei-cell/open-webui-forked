"""
title: Image Style Workspace
author: Andrei
description: Saves structured image style profiles and their current reference images as transactional artifacts inside the active Files Workspace.
required_open_webui_version: 0.11.3
version: 0.2.0
license: MIT
"""

import json
import os
import re
import unicodedata

from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from open_webui.models.config import Config
from open_webui.models.users import Users
from open_webui.utils.tools import get_terminal_tools


class Tools:
    """Create self-contained image-style artifacts in the active Files Workspace."""

    ARTIFACT_ROOT = "/image-styles"
    STAGING_ROOT = "/__image_style_staging__"

    SUPPORTED_MIME_TYPES = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }

    IMAGE_NAME_EXTENSIONS = {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
    }

    MAX_REFERENCES = 32
    MAX_REFERENCE_BYTES = 32 * 1024 * 1024
    MAX_TOTAL_BYTES = 128 * 1024 * 1024
    MAX_STYLE_ID_ATTEMPTS = 100

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

    def _slugify(self, name: str) -> str:
        """Create a safe Unicode filesystem style ID."""
        value = unicodedata.normalize("NFKC", name).strip().casefold()

        result: List[str] = []
        previous_dash = False

        for char in value:
            if char.isalnum():
                result.append(char)
                previous_dash = False
            elif result and not previous_dash:
                result.append("-")
                previous_dash = True

        slug = "".join(result).strip("-")
        if not slug:
            slug = "style"

        slug = slug[:96].rstrip("-")
        return slug or "style"

    def _style_id_for_attempt(self, base_id: str, attempt: int) -> str:
        return base_id if attempt == 1 else f"{base_id}-{attempt}"

    def _json(self, value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    # ------------------------------------------------------------------
    # Active Files Workspace resolution
    # ------------------------------------------------------------------

    async def _get_workspace_tools(
        self,
        __request__: Any,
        __metadata__: Dict[str, Any],
        __user__: Dict[str, Any],
        __oauth_token__: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[Dict[str, Dict[str, Any]]], Optional[str]]:
        """
        Resolve the active Files Workspace through Open WebUI's trusted
        terminal connection registry.

        The browser/model never supplies a host path, base URL, or bearer token
        to this Tool. The selected terminal ID comes from trusted request
        metadata and the actual connection/auth is resolved server-side.
        """
        metadata = __metadata__ if isinstance(__metadata__, dict) else {}
        terminal_id = self._clean_string(metadata.get("terminal_id"))

        if not terminal_id:
            return None, "no_files_workspace"

        connections = await Config.get("terminal_server.connections", []) or []
        connection = next(
            (
                item
                for item in connections
                if isinstance(item, dict) and item.get("id") == terminal_id
            ),
            None,
        )

        if connection is None or not connection.get("enabled", True):
            return None, "files_workspace_unavailable"

        connection_config = connection.get("config") or {}
        is_files_workspace = (
            connection_config.get("files_workspace") is True
            or connection_config.get("chat_uploads") == "filesystem"
        )

        if not is_files_workspace:
            return None, "selected_terminal_is_not_files_workspace"

        user_id = self._clean_string((__user__ or {}).get("id"))
        if not user_id:
            return None, "user_context_unavailable"

        user = await Users.get_user_by_id(user_id)
        if user is None:
            return None, "user_context_unavailable"

        tools_result = await get_terminal_tools(
            __request__,
            terminal_id,
            user,
            {
                "__metadata__": metadata,
                "__oauth_token__": __oauth_token__,
            },
        )

        if isinstance(tools_result, tuple):
            workspace_tools = tools_result[0]
        else:
            workspace_tools = tools_result

        required = {
            "checksum_file",
            "copy_path",
            "create_directory",
            "write_file",
            "move_path",
            "delete_path",
        }
        missing = sorted(required - set(workspace_tools))

        if missing:
            raise RuntimeError(
                "Active Files Workspace is missing required operations: "
                + ", ".join(missing)
            )

        return workspace_tools, None

    async def _call_raw(
        self,
        workspace_tools: Dict[str, Dict[str, Any]],
        operation: str,
        **params: Any,
    ) -> Any:
        tool = workspace_tools.get(operation)
        if not tool:
            raise RuntimeError(f"Files Workspace operation is unavailable: {operation}")

        result = await tool["callable"](**params)

        if isinstance(result, tuple):
            return result[0]
        return result

    def _tool_error(self, result: Any) -> Optional[str]:
        if isinstance(result, dict):
            error = result.get("error")
            if isinstance(error, str) and error.strip():
                return error.strip()
        return None

    async def _call(
        self,
        workspace_tools: Dict[str, Dict[str, Any]],
        operation: str,
        **params: Any,
    ) -> Any:
        result = await self._call_raw(
            workspace_tools,
            operation,
            **params,
        )
        error = self._tool_error(result)
        if error:
            raise RuntimeError(f"{operation} failed: {error}")
        return result

    async def _cleanup_staging(
        self,
        workspace_tools: Dict[str, Dict[str, Any]],
        staging_path: Optional[str],
    ) -> Optional[str]:
        if not staging_path:
            return None

        try:
            result = await self._call_raw(
                workspace_tools,
                "delete_path",
                path=staging_path,
            )
            error = self._tool_error(result)
            if error and "404" not in error and "not found" not in error.lower():
                return error
        except Exception as exc:
            return str(exc)

        return None

    # ------------------------------------------------------------------
    # Current-turn reference discovery and validation
    # ------------------------------------------------------------------

    def _filesystem_attachments(self, files: Any) -> List[Dict[str, Any]]:
        if not isinstance(files, list):
            return []
        return [
            item
            for item in files
            if isinstance(item, dict) and item.get("type") == "filesystem"
        ]

    def _attachment_path(self, item: Dict[str, Any]) -> str:
        for candidate in (
            item.get("path"),
            (item.get("file") or {}).get("path")
            if isinstance(item.get("file"), dict)
            else None,
            item.get("id"),
            item.get("url"),
        ):
            if isinstance(candidate, str) and candidate.strip():
                value = candidate.strip()
                return value if value.startswith("/") else f"/{value}"
        return ""

    def _attachment_name(self, item: Dict[str, Any], path: str) -> str:
        name = item.get("name")
        if isinstance(name, str) and name.strip():
            return os.path.basename(name.strip())
        return PurePosixPath(path).name

    async def _inspect_references(
        self,
        workspace_tools: Dict[str, Dict[str, Any]],
        files: Any,
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        attachments = self._filesystem_attachments(files)

        if not attachments:
            raise ValueError(
                "No current-turn Files Workspace attachments are available."
            )

        inspected_images: List[Dict[str, Any]] = []
        ignored_non_images = 0
        total_bytes = 0

        for item in attachments:
            path = self._attachment_path(item)
            if not path:
                continue

            original_name = self._attachment_name(item, path)
            result = await self._call(
                workspace_tools,
                "checksum_file",
                path=path,
            )

            if not isinstance(result, dict):
                raise RuntimeError(
                    f"checksum_file returned an invalid response for {original_name or path}."
                )

            mime_type = self._clean_string(result.get("mime_type")).lower()
            size_bytes = result.get("size")
            sha256 = self._clean_string(result.get("sha256")).lower()

            if mime_type not in self.SUPPORTED_MIME_TYPES:
                extension = PurePosixPath(original_name).suffix.casefold()
                if extension in self.IMAGE_NAME_EXTENSIONS:
                    raise ValueError(
                        f"Reference image {original_name!r} failed content-signature validation "
                        f"(detected MIME: {mime_type or 'unknown'})."
                    )
                ignored_non_images += 1
                continue

            if not isinstance(size_bytes, int) or size_bytes <= 0:
                raise ValueError(f"Reference image {original_name!r} has an invalid size.")

            if size_bytes > self.MAX_REFERENCE_BYTES:
                raise ValueError(
                    f"Reference image {original_name!r} exceeds the maximum allowed "
                    f"size of {self.MAX_REFERENCE_BYTES} bytes."
                )

            if not re.fullmatch(r"[0-9a-f]{64}", sha256):
                raise ValueError(
                    f"Reference image {original_name!r} returned an invalid SHA-256 digest."
                )

            total_bytes += size_bytes
            if total_bytes > self.MAX_TOTAL_BYTES:
                raise ValueError(
                    "Combined reference image size exceeds the maximum allowed total "
                    f"of {self.MAX_TOTAL_BYTES} bytes."
                )

            inspected_images.append(
                {
                    "source_path": path,
                    "original_name": original_name or None,
                    "mime_type": mime_type,
                    "extension": self.SUPPORTED_MIME_TYPES[mime_type],
                    "size_bytes": size_bytes,
                    "sha256": sha256,
                }
            )

        if not inspected_images:
            raise ValueError(
                "No supported JPEG, PNG, or WebP reference images were found "
                "among the current-turn Files Workspace attachments."
            )

        if len(inspected_images) > self.MAX_REFERENCES:
            raise ValueError(
                f"Too many reference images: {len(inspected_images)}. "
                f"Maximum is {self.MAX_REFERENCES}."
            )

        unique: List[Dict[str, Any]] = []
        seen_hashes = set()
        duplicates_skipped = 0

        for reference in inspected_images:
            digest = reference["sha256"]
            if digest in seen_hashes:
                duplicates_skipped += 1
                continue
            seen_hashes.add(digest)
            unique.append(reference)

        if not unique:
            raise ValueError("No unique reference images remain after deduplication.")

        return unique, duplicates_skipped, ignored_non_images

    # ------------------------------------------------------------------
    # Artifact construction
    # ------------------------------------------------------------------

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
            "generation_guidance": self._clean_string(generation_guidance),
            "avoid": self._clean_string(avoid),
        }

    def _document(
        self,
        style_id: str,
        name: str,
        created_at: str,
        references: List[Dict[str, Any]],
        duplicates_skipped: int,
        provenance_status: str,
        source_type: str,
        source_title: str,
        creator: str,
        style_profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "artifact_type": "image_style_profile",
            "style_id": style_id,
            "name": name,
            "created_at": created_at,
            "reference_count": len(references),
            "duplicates_skipped": duplicates_skipped,
            "references": references,
            "provenance": {
                "status": provenance_status,
                "source_type": source_type or None,
                "title": source_title or None,
                "creator": creator or None,
            },
            "style": style_profile,
        }

    async def save_image_style_to_workspace(
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
        __files__: List[dict] = [],
        __metadata__: Dict[str, Any] = {},
        __user__: Dict[str, Any] = {},
        __request__: Any = None,
        __oauth_token__: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Save one extracted visual style and the reference images attached to
        the current user turn as a self-contained artifact in the active
        Files Workspace.

        Reference paths, bytes, hashes, MIME types, sizes, destination paths,
        and workspace credentials are resolved internally. Do not ask the
        model/user to provide them.

        This creates a project artifact only. It does not publish anything to
        the global Image Style Library.
        """
        name = self._clean_string(suggested_name)
        if not name:
            return self._json(
                {
                    "status": "error",
                    "reason": "suggested_name must not be empty",
                }
            )
        if len(name) > 160:
            return self._json(
                {
                    "status": "error",
                    "reason": "suggested_name is too long",
                }
            )

        provenance_status = self._clean_string(provenance_status).lower() or "unknown"
        allowed_provenance = {"unknown", "user_provided", "verified"}
        if provenance_status not in allowed_provenance:
            return self._json(
                {
                    "status": "error",
                    "reason": (
                        "provenance_status must be one of: "
                        "unknown, user_provided, verified"
                    ),
                }
            )

        source_type = self._clean_string(source_type)
        source_title = self._clean_string(source_title)
        creator = self._clean_string(creator)
        if provenance_status == "unknown":
            source_type = ""
            source_title = ""
            creator = ""

        if __request__ is None:
            return self._json(
                {
                    "status": "error",
                    "reason": "Open WebUI request context is unavailable",
                }
            )

        staging_path: Optional[str] = None
        workspace_tools: Optional[Dict[str, Dict[str, Any]]] = None

        try:
            workspace_tools, workspace_error = await self._get_workspace_tools(
                __request__,
                __metadata__,
                __user__,
                __oauth_token__,
            )

            if workspace_tools is None:
                return self._json(
                    {
                        "status": "not_saved",
                        "reason": workspace_error or "no_files_workspace",
                        "instruction": (
                            "The style can still be returned in chat, but no project "
                            "artifact was created. Do not publish it to the global "
                            "Image Style Library as a fallback."
                        ),
                    }
                )

            references, duplicates_skipped, ignored_non_images = await self._inspect_references(
                workspace_tools,
                __files__,
            )

            style_profile = self._style_profile(
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

            base_id = self._slugify(name)
            created_at = self._now_iso()
            staging_path = f"{self.STAGING_ROOT}/style-{uuid4().hex}"
            references_path = f"{staging_path}/references"

            await self._call(
                workspace_tools,
                "create_directory",
                path=references_path,
            )

            reference_metadata: List[Dict[str, Any]] = []

            for index, reference in enumerate(references, start=1):
                filename = f"ref_{index:03d}.{reference['extension']}"
                relative_path = f"references/{filename}"
                destination = f"{staging_path}/{relative_path}"

                await self._call(
                    workspace_tools,
                    "copy_path",
                    source=reference["source_path"],
                    destination=destination,
                )

                copied = await self._call(
                    workspace_tools,
                    "checksum_file",
                    path=destination,
                )

                if not isinstance(copied, dict):
                    raise RuntimeError(
                        f"Invalid checksum response for copied reference {filename}."
                    )

                copied_size = copied.get("size")
                copied_sha256 = self._clean_string(copied.get("sha256")).lower()
                copied_mime = self._clean_string(copied.get("mime_type")).lower()

                if (
                    copied_size != reference["size_bytes"]
                    or copied_sha256 != reference["sha256"]
                    or copied_mime != reference["mime_type"]
                ):
                    raise RuntimeError(
                        f"Copied reference verification failed for {filename}."
                    )

                item: Dict[str, Any] = {
                    "filename": relative_path,
                    "mime_type": reference["mime_type"],
                    "size_bytes": reference["size_bytes"],
                    "sha256": reference["sha256"],
                    "source": "open_webui_files_workspace_attachment",
                }
                if reference.get("original_name"):
                    item["original_name"] = reference["original_name"]

                reference_metadata.append(item)

            # style.json is deliberately written only after every binary
            # reference has been copied and verified in staging.
            style_id = self._style_id_for_attempt(base_id, 1)
            document = self._document(
                style_id,
                name,
                created_at,
                reference_metadata,
                duplicates_skipped,
                provenance_status,
                source_type,
                source_title,
                creator,
                style_profile,
            )

            async def write_staging_document() -> None:
                content = json.dumps(
                    document,
                    ensure_ascii=False,
                    indent=2,
                ) + "\n"
                await self._call(
                    workspace_tools,
                    "write_file",
                    path=f"{staging_path}/style.json",
                    content=content,
                )

            await write_staging_document()

            final_path: Optional[str] = None

            for attempt in range(1, self.MAX_STYLE_ID_ATTEMPTS + 1):
                candidate = self._style_id_for_attempt(base_id, attempt)
                if document["style_id"] != candidate:
                    document["style_id"] = candidate
                    await write_staging_document()

                candidate_path = f"{self.ARTIFACT_ROOT}/{candidate}"
                move_result = await self._call_raw(
                    workspace_tools,
                    "move_path",
                    source=staging_path,
                    destination=candidate_path,
                )
                move_error = self._tool_error(move_result)

                if not move_error:
                    style_id = candidate
                    final_path = candidate_path
                    staging_path = None
                    break

                if "409" in move_error or "destination already exists" in move_error.lower():
                    continue

                raise RuntimeError(f"move_path failed: {move_error}")

            if final_path is None:
                raise RuntimeError(
                    "Could not allocate a unique style_id after "
                    f"{self.MAX_STYLE_ID_ATTEMPTS} attempts."
                )

            # Verify the published references at their final paths before
            # declaring the artifact complete.
            for reference in reference_metadata:
                final_reference_path = f"{final_path}/{reference['filename']}"
                verified = await self._call(
                    workspace_tools,
                    "checksum_file",
                    path=final_reference_path,
                )

                if not isinstance(verified, dict) or (
                    verified.get("size") != reference["size_bytes"]
                    or self._clean_string(verified.get("sha256")).lower()
                    != reference["sha256"]
                    or self._clean_string(verified.get("mime_type")).lower()
                    != reference["mime_type"]
                ):
                    # This final directory was just created by this call. If
                    # post-publish verification fails, remove it so an invalid
                    # artifact is not reported or left as complete.
                    cleanup_result = await self._call_raw(
                        workspace_tools,
                        "delete_path",
                        path=final_path,
                    )
                    cleanup_error = self._tool_error(cleanup_result)
                    result = {
                        "status": "error",
                        "reason": "post_publish_reference_verification_failed",
                        "path": final_path,
                    }
                    if cleanup_error:
                        result["cleanup_warning"] = cleanup_error
                    return self._json(result)

            return self._json(
                {
                    "status": "saved",
                    "artifact_type": "image_style_profile",
                    "style_id": style_id,
                    "name": name,
                    "path": final_path,
                    "reference_count": len(reference_metadata),
                    "duplicates_skipped": duplicates_skipped,
                    "ignored_non_image_attachments": ignored_non_images,
                }
            )

        except Exception as exc:
            cleanup_warning = None
            if workspace_tools is not None and staging_path is not None:
                cleanup_warning = await self._cleanup_staging(
                    workspace_tools,
                    staging_path,
                )

            result = {
                "status": "error",
                "reason": str(exc),
            }
            if cleanup_warning:
                result["cleanup_warning"] = cleanup_warning
                result["staging_path"] = staging_path

            return self._json(result)
