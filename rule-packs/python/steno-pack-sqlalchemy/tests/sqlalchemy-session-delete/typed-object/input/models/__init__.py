from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "project"
    id: Mapped[int] = mapped_column(primary_key=True)


class ProjectData(Base):
    __tablename__ = "project_data"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int]
    data_source_id: Mapped[int]
