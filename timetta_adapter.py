from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from ..models import CanonicalTask


class TimettaAdapter:
    DEFAULT_BASE_URL = "https://api.timetta.com/odata"

    def __init__(self, settings: dict[str, str]) -> None:
        self.settings = settings
        self.base_url = (settings.get("TIMETTA_BASE_URL") or self.DEFAULT_BASE_URL).rstrip("/")
        self.auth_scheme = settings.get("TIMETTA_AUTH_SCHEME", "Bearer") or "Bearer"
        self.token = settings.get("TIMETTA_API_TOKEN", "")

    @classmethod
    def issue_type_candidates(cls, issue_type: str) -> tuple[str, ...]:
        normalized = (issue_type or "task").lower()
        if normalized == "epic":
            return ("EPIC",)
        if normalized == "bug":
            return ("BUG",)
        if normalized == "hotfix":
            return ("BUG", "TASK", "ASSIGNMENT", "DEVELOPMENT")
        if normalized == "research":
            return ("TASK", "ASSIGNMENT", "DOCUMENT", "DEVELOPMENT")
        if normalized == "tech_debt":
            return ("TASK", "DEVELOPMENT", "ASSIGNMENT")
        return ("TASK", "ASSIGNMENT", "DEVELOPMENT")

    def validate_settings(self) -> list[str]:
        missing = []
        if not self.token:
            missing.append("TIMETTA_API_TOKEN")
        return missing

    def payload_preview(self, task: CanonicalTask) -> dict[str, Any]:
        return {
            "project_id": task.project_id or self.settings.get("TIMETTA_DEFAULT_PROJECT_ID", ""),
            "task_kind": task.task_kind,
            "issue_type": task.issue_type,
            "timetta_issue_type_candidates": list(self.issue_type_candidates(task.issue_type)),
            "summary": task.summary,
            "description": task.rendered_description,
            "parent_id": task.parent_key,
            "ready": not self.validate_settings(),
        }

    def create_issue(self, task: CanonicalTask, parent_id: str | None = None) -> dict[str, Any]:
        missing = self.validate_settings()
        if missing:
            return {
                "created": False,
                "reason": "Timetta settings are incomplete",
                "missing_settings": missing,
                "payload_preview": self.payload_preview(task),
            }

        project_id = task.project_id or self.settings.get("TIMETTA_DEFAULT_PROJECT_ID", "")
        if not project_id:
            raise RuntimeError("Timetta create requires project_id or TIMETTA_DEFAULT_PROJECT_ID")

        issue_type = self._resolve_issue_type(project_id, task)
        priority = self._resolve_priority()
        resolved_parent_id = parent_id or task.parent_key or ""
        body = {
            "name": task.summary,
            "description": task.rendered_description,
            "projectId": project_id,
            "typeId": issue_type["id"],
            "priorityId": priority["id"],
        }

        assignee_id = self.settings.get("TIMETTA_DEFAULT_ASSIGNEE_ID", "")
        if assignee_id:
            body["assignedId"] = assignee_id

        project_task_id = self._resolve_project_task_id(task, project_id)
        if project_task_id:
            body["projectTaskId"] = project_task_id

        if resolved_parent_id:
            body["parentId"] = resolved_parent_id

        created = self._request("POST", "/Issues", body)
        return {
            "created": True,
            "id": created.get("id"),
            "key": created.get("code"),
            "self": f"{self.base_url}/Issues({created.get('id')})" if created.get("id") else "",
            "project_id": project_id,
            "type_id": issue_type["id"],
            "type_code": issue_type.get("code", ""),
            "priority_id": priority["id"],
            "priority_code": priority.get("code", ""),
            "parent_id": resolved_parent_id,
            "project_task_id": body.get("projectTaskId", ""),
            "raw": created,
        }

    def delete_issue(self, issue_id: str) -> None:
        if issue_id:
            self._request("DELETE", f"/Issues({issue_id})")

    def get_issue(self, issue_id: str) -> dict[str, Any]:
        return self._request("GET", f"/Issues({issue_id})")

    def get_issue_by_key(self, issue_key: str) -> dict[str, Any]:
        if not issue_key:
            raise RuntimeError("Timetta issue key is required")
        safe_key = issue_key.replace("'", "''")
        issues = self._fetch_collection(f"/Issues?$filter=code%20eq%20'{safe_key}'&$top=2")
        if issues:
            return issues[0]
        if issue_key.isdigit():
            return self.get_issue(issue_key)
        raise RuntimeError(f"Timetta issue not found by key: {issue_key}")

    def update_issue(self, issue_id: str, *, summary: str | None = None, description: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if summary is not None:
            body["name"] = summary
        if description is not None:
            body["description"] = description
        if not body:
            return self.get_issue(issue_id)
        return self._request("PATCH", f"/Issues({issue_id})", body)

    def list_issue_links(self, *, source_id: str | None = None, destination_id: str | None = None) -> list[dict[str, Any]]:
        filters: list[str] = []
        if source_id:
            filters.append(f"sourceId eq {source_id}")
        if destination_id:
            filters.append(f"destinationId eq {destination_id}")
        path = "/IssueLinks?$top=200"
        if filters:
            joined = " and ".join(filters).replace(" ", "%20")
            path = f"/IssueLinks?$filter={joined}&$top=200"
        return self._fetch_collection(path)

    def resolve_issue_link_type(self, preferred_name: str = "Реализация") -> dict[str, Any]:
        items = self._fetch_collection("/IssueLinkTypes?$top=200")
        for item in items:
            if (item.get("name") or "") == preferred_name and item.get("id"):
                return item
        for item in items:
            if (item.get("name") or "") == "Связь" and item.get("id"):
                return item
        if items:
            return items[0]
        raise RuntimeError("Timetta issue link types catalog is empty")

    def create_issue_link(self, *, source_id: str, destination_id: str, link_type_id: str) -> dict[str, Any]:
        body = {
            "sourceId": source_id,
            "destinationId": destination_id,
            "linkTypeId": link_type_id,
        }
        return self._request("POST", "/IssueLinks", body)

    def _request(self, method: str, path_or_url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = path_or_url if path_or_url.startswith("http") else f"{self.base_url}{path_or_url}"
        headers = {
            "Authorization": f"{self.auth_scheme} {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
        }
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url=url, method=method, headers=headers, data=data)
        try:
            with urllib.request.urlopen(request) as response:
                payload = response.read().decode("utf-8").strip()
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Timetta request failed: {exc.code} {payload}") from exc

    def _multipart_request(
        self,
        method: str,
        path_or_url: str,
        *,
        file_field: str,
        file_path: Path,
        filename: str | None = None,
        form_fields: dict[str, str] | None = None,
        query_params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = path_or_url if path_or_url.startswith("http") else f"{self.base_url}{path_or_url}"
        if query_params:
            encoded = urllib.parse.urlencode(query_params)
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{encoded}"
        upload_name = filename or file_path.name
        mime_type = mimetypes.guess_type(upload_name)[0] or "application/octet-stream"
        boundary = f"----CodexBoundary{uuid.uuid4().hex}"
        body = bytearray()
        for key, value in (form_fields or {}).items():
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{upload_name}"\r\n'
            ).encode("utf-8")
        )
        body.extend(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))
        body.extend(file_path.read_bytes())
        body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))
        headers = {
            "Authorization": f"{self.auth_scheme} {self.token}",
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        request = urllib.request.Request(url=url, method=method, headers=headers, data=bytes(body))
        try:
            with urllib.request.urlopen(request) as response:
                payload = response.read().decode("utf-8").strip()
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Timetta multipart request failed: {exc.code} {payload}") from exc

    def _fetch_collection(self, path: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_url = path if path.startswith("http") else f"{self.base_url}{path}"
        while next_url:
            payload = self._request("GET", next_url)
            values = payload.get("value", [])
            if isinstance(values, list):
                items.extend(values)
            next_url = payload.get("@odata.nextLink", "")
        return items

    def _resolve_issue_type(self, project_id: str, task: CanonicalTask) -> dict[str, str]:
        preferred_codes = self.issue_type_candidates(task.issue_type)
        issue_types = task.tracker_metadata.get("supported_issue_types") or []
        if not issue_types:
            allowed_types = self._fetch_collection("/ProjectIssueTypes?$select=id,projectId,issueTypeId&$expand=issueType($select=id,name,code,isActive)&$top=500")
            issue_types = [
                {
                    "id": str((item.get("issueType") or {}).get("id", "")),
                    "name": (item.get("issueType") or {}).get("name", ""),
                    "code": (item.get("issueType") or {}).get("code", ""),
                }
                for item in allowed_types
                if str(item.get("projectId") or "") == project_id and item.get("issueType")
            ]
        active_types = [item for item in issue_types if item.get("id")]
        if not active_types:
            raise RuntimeError(f"Timetta project {project_id} has no available issue types")

        for code in preferred_codes:
            for item in active_types:
                if (item.get("code") or "").upper() == code:
                    return {"id": str(item["id"]), "name": item.get("name", ""), "code": item.get("code", "")}

        available = ", ".join(sorted(filter(None, {(item.get("code") or item.get("name") or "").upper() for item in active_types})))
        wanted = ", ".join(preferred_codes)
        raise RuntimeError(f"Timetta project {project_id} does not support issue type candidates [{wanted}]. Available: {available}")

    def _resolve_priority(self) -> dict[str, str]:
        priorities = self._fetch_collection("/DirectoryEntries?$filter=isActive%20eq%20true&$select=id,name,code,isDefault&$top=300")
        preferred_codes = (self.settings.get("TIMETTA_DEFAULT_PRIORITY_CODE", "").upper(), "NORMAL", "MEDIUM", "DEFAULT")
        for code in preferred_codes:
            if not code:
                continue
            for item in priorities:
                if (item.get("code") or "").upper() == code:
                    return {"id": str(item["id"]), "name": item.get("name", ""), "code": item.get("code", "")}
        for item in priorities:
            if item.get("isDefault") and item.get("id"):
                return {"id": str(item["id"]), "name": item.get("name", ""), "code": item.get("code", "")}
        for item in priorities:
            if item.get("id"):
                return {"id": str(item["id"]), "name": item.get("name", ""), "code": item.get("code", "")}
        raise RuntimeError("Timetta priority catalog is empty")

    def _resolve_project_task_id(self, task: CanonicalTask, project_id: str) -> str:
        explicit = task.tracker_metadata.get("project_task_id", "") or self.settings.get("TIMETTA_DEFAULT_PROJECT_TASK_ID", "")
        if explicit:
            return explicit
        if not task.tracker_metadata.get("project_task_match_hint"):
            return ""

        match_hints = [hint.strip().lower() for hint in str(task.tracker_metadata.get("project_task_match_hint", "")).split("|") if hint.strip()]
        candidates = [
            item
            for item in self._fetch_collection("/ProjectTasks?$select=id,name,fullPath,projectId&$top=500")
            if str(item.get("projectId") or "") == project_id
        ]
        for item in candidates:
            haystack = f"{item.get('name', '')} {item.get('fullPath', '')}".lower()
            if all(hint in haystack for hint in match_hints):
                return str(item.get("id", ""))
        return ""

    def list_entity_files(self, entity_id: str) -> list[dict[str, Any]]:
        if not entity_id:
            return []
        return self._fetch_collection(f"/Files/WP.GetFilesMetadata(entityId={entity_id})")

    def list_issue_attachments(self, issue_key_or_id: str) -> list[dict[str, Any]]:
        issue = self.get_issue_by_key(issue_key_or_id)
        return self.list_entity_files(str(issue.get("id") or ""))

    def attach_entity_file(
        self,
        *,
        entity_type: str,
        entity_id: str,
        file_path: str | Path,
        filename: str | None = None,
    ) -> dict[str, Any]:
        path = Path(file_path)
        if not path.exists():
            raise RuntimeError(f"Attachment file not found: {path}")
        return self._multipart_request(
            "POST",
            "/Files/WP.UploadFile",
            file_field="attachment",
            file_path=path,
            filename=filename,
            form_fields={
                "entityType": entity_type,
                "entityId": entity_id,
            },
        )

    def attach_issue_file(self, issue_key_or_id: str, file_path: str | Path, *, filename: str | None = None) -> dict[str, Any]:
        issue = self.get_issue_by_key(issue_key_or_id)
        issue_id = str(issue.get("id") or "")
        if not issue_id:
            raise RuntimeError(f"Timetta issue has empty id: {issue_key_or_id}")
        return self.attach_entity_file(entity_type="Issue", entity_id=issue_id, file_path=file_path, filename=filename)

    def delete_file(self, file_id: str) -> dict[str, Any]:
        if not file_id:
            raise RuntimeError("Timetta file id is required")
        return self._request("POST", "/Files/WP.DeleteFile", {"fileKey": file_id})
