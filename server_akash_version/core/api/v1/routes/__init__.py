"""API routes."""

from core.api.v1.routes.auth import router as auth_router
from core.api.v1.routes.data_sources import router as data_sources_router
from core.api.v1.routes.excel_agent import router as excel_agent_router

__all__ = ["auth_router", "data_sources_router", "excel_agent_router"]
