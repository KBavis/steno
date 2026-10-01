from sqlalchemy import update

from models import ProjectData


async def detach(db, data_source_id):
    await db.execute(
        update(ProjectData).where(ProjectData.data_source_id == data_source_id).values(project_id=None)
    )
