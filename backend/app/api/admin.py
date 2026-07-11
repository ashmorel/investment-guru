from fastapi import APIRouter

from app.api.deps import AdminUser

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/ping")
async def ping(user: AdminUser) -> dict[str, bool]:
    """Admin-only ping endpoint."""
    return {"ok": True}
