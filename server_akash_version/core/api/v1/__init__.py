from fastapi import APIRouter

from core.api.v1.routes.auth import router as auth_router
from core.api.v1.routes.data_sources import router as data_sources_router
from core.api.v1.routes.excel_agent import router as excel_agent_router

api_router = APIRouter(prefix="/v1")

# Include all route modules
api_router.include_router(auth_router)
api_router.include_router(data_sources_router)
api_router.include_router(excel_agent_router)


__all__ = ["api_router"]
