import asyncio


class AgentService:
    async def warm_cache(self, project_id):
        ...

    async def start(self, project_id):
        asyncio.create_task(self.warm_cache(project_id))
        await asyncio.sleep(0)          # not a task
