"""In-memory workbook + conversation state store (hackathon scope).

v1.5: ChatSessionStore now returns ConversationState. The old ChatSession
alias is kept for one release for frontend compatibility.
"""
from __future__ import annotations

import threading
from typing import Optional

from .conversation import ConversationState, new_session_id
from .models import WorkbookModel


class WorkbookStore:
    def __init__(self) -> None:
        self._wbs: dict[str, WorkbookModel] = {}
        self._lock = threading.Lock()

    def put(self, wb: WorkbookModel) -> None:
        with self._lock:
            self._wbs[wb.workbook_id] = wb

    def get(self, wid: str) -> Optional[WorkbookModel]:
        return self._wbs.get(wid)

    def list(self) -> list[WorkbookModel]:
        return list(self._wbs.values())


store = WorkbookStore()


class ChatSessionStore:
    """Manages ConversationState objects keyed by session_id."""

    def __init__(self) -> None:
        self._sessions: dict[str, ConversationState] = {}
        self._lock = threading.Lock()

    def create(self, workbook_id: str) -> ConversationState:
        sid = new_session_id()
        s = ConversationState(session_id=sid, workbook_id=workbook_id)
        with self._lock:
            self._sessions[sid] = s
        return s

    def get(self, sid: str) -> Optional[ConversationState]:
        return self._sessions.get(sid)

    def get_or_create(self, sid: Optional[str], workbook_id: str) -> ConversationState:
        if sid:
            s = self.get(sid)
            if s and s.workbook_id == workbook_id:
                return s
        return self.create(workbook_id)

    def delete(self, sid: str) -> bool:
        with self._lock:
            return self._sessions.pop(sid, None) is not None


chat_store = ChatSessionStore()


# --- Backward-compat alias (to be removed in v2) ---
ChatSession = ConversationState
