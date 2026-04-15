"""In-memory workbook store (hackathon scope)."""
from __future__ import annotations

import threading

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
