from sqlalchemy.ext.asyncio import AsyncSession

from models import Project


async def remove(session: AsyncSession, project_id: int):
    # Annotated so the resolver knows the type. An unannotated `project = await session.get(Project, …)`
    # isn't followed yet: its type comes from the argument (a known gap in the small resolver).
    project: Project | None = await session.get(Project, project_id)
    await session.delete(project)
