from sqlalchemy.orm import Session

from models import Project


def parent_of(session: Session, project_id: int):
    return session.get_one(Project, project_id).parent_id
