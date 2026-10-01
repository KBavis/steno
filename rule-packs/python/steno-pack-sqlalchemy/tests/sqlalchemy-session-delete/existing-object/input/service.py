from sqlalchemy.ext.asyncio import AsyncSession

from models import Project


async def remove(session: AsyncSession, project_id: int):
    project = await session.get(Project, project_id)
    await session.delete(project)
