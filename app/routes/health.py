from fastapi import APIRouter, HTTPException
from app.db.session import get_database
from app.core.config import settings

router = APIRouter()


@router.get("/health")
async def health_check():
    try:
        db = await get_database()
        await db.command("ping")
        
        return {
            "status": "healthy",
            "environment": settings.ENVIRONMENT,
            "database": "connected",
            "storage": settings.STORAGE_BACKEND
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Service unhealthy: {str(e)}")
