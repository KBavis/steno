from sqlalchemy.ext.asyncio import AsyncSession

from models import Project


async def load(session: AsyncSession, project_id: int):
    return await session.get(Project, project_id)


def lookup(cache: dict):
    return cache.get(Project, None)   # dict.get: excluded by `where`
