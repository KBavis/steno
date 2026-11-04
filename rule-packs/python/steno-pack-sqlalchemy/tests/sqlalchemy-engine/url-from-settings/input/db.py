from sqlalchemy.ext.asyncio import create_async_engine

from config import settings

engine = create_async_engine(settings.ASYNC_DB_URL, pool_size=10)
