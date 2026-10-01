from sqlalchemy import func, select

from models import Project, ProjectData


async def projects_for_source(db, data_source_id):
    stmt = select(Project).join(ProjectData).where(ProjectData.data_source_id == data_source_id)
    return (await db.execute(stmt)).scalars().all()


async def project_count(db):
    return await db.scalar(select(func.count()))      # not an entity: no edge
