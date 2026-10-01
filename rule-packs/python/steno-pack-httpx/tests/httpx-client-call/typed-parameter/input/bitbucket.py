import httpx

API = "https://bitbucket.example.com/rest/api/1.0"


class BitbucketRepository:
    async def fetch_pull_request(self, client: httpx.AsyncClient, pr_id: int):
        resp = await client.get(f"{API}/projects/PAY/repos/svc/pull-requests/{pr_id}")
        return resp.json()
