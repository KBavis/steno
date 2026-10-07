import asyncio


class ChunkService:
    def _save(self, nodes):
        ...

    def _hash(self, buffer):
        ...

    async def store(self, nodes, buffer):
        await asyncio.to_thread(self._save, nodes)
        await asyncio.to_thread(
            self._hash,
            buffer,
        )


def load_model():
    ...


async def warm():
    return await asyncio.to_thread(load_model)    # no arguments after the function
