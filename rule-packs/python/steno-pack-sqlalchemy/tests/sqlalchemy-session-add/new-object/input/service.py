from sqlalchemy.ext.asyncio import AsyncSession

from models import Project


class ProjectService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self):
        project = Project()
        self.db.add(project)
        await self.db.flush()

    def tag(self, tags: set):
        tags.add("new")               # set.add: excluded by `where`
