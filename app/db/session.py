from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from app.core.config import settings

client: AsyncIOMotorClient = None
database: AsyncIOMotorDatabase = None


async def get_database() -> AsyncIOMotorDatabase:
    global client, database
    if database is None:
        client = AsyncIOMotorClient(settings.MONGODB_URI)
        database = client[settings.MONGODB_DB_NAME]
    return database


async def close_database():
    global client
    if client:
        client.close()
