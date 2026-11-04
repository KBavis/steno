from sqlalchemy import create_engine

engine = create_engine("postgresql+psycopg://app:secret@db.local:5432/orders", pool_pre_ping=True)
