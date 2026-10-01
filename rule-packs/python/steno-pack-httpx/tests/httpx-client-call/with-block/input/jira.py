import httpx

SEARCH_PATH = "/rest/api/2/search"


class JiraIssueTracker:
    def __init__(self, url: str):
        self.url = url          # known only at runtime

    async def search(self, jql: str):
        base_url = self.url.rstrip("/")
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{base_url}{SEARCH_PATH}", json={"jql": jql})
            return resp.json()

    async def status(self):
        async with httpx.AsyncClient() as client:
            return await client.get("https://status.atlassian.com/api/v2/status.json")
