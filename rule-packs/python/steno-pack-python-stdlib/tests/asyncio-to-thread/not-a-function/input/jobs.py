import asyncio


def make_job():
    ...


async def run():
    await asyncio.to_thread(make_job())   # runs whatever make_job returned, not make_job
