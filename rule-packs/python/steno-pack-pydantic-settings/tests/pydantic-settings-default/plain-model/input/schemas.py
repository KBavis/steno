from pydantic import BaseModel


class JobResponse(BaseModel):
    status: str = "pending"
