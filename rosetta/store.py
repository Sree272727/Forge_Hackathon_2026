"""In-memory workbook + chat session store (hackathon scope)."""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

from .models import WorkbookModel


class WorkbookStore:
    def __init__(self) -> None:
        self._wbs: dict[str, WorkbookModel] = {}
        self._lock = threading.Lock()

    def put(self, wb: WorkbookModel) -> None:
        with self._lock:
            self._wbs[wb.workbook_id] = wb

    def get(self, wid: str) -> WorkbookModel | None:
        return self._wbs.get(wid)

    def list(self) -> list[WorkbookModel]:
        return list(self._wbs.values())


store = WorkbookStore()


@dataclass
class ChatSession:
    session_id: str
    workbook_id: str
    messages: list[dict] = field(default_factory=list)  # [{role, content}]
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class ChatSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}
        self._lock = threading.Lock()

    def create(self, workbook_id: str) -> ChatSession:
        sid = uuid.uuid4().hex[:12]
        s = ChatSession(session_id=sid, workbook_id=workbook_id)
        with self._lock:
            self._sessions[sid] = s
        return s

    def get(self, sid: str) -> ChatSession | None:
        return self._sessions.get(sid)

    def get_or_create(self, sid: str | None, workbook_id: str) -> ChatSession:
        if sid:
            s = self.get(sid)
            if s and s.workbook_id == workbook_id:
                return s
        return self.create(workbook_id)


chat_store = ChatSessionStore()
