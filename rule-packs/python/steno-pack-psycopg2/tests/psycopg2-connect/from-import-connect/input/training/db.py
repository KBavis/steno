import os
from psycopg2 import connect


def connect_db():
    db_name = os.getenv("DB_NAME")
    password = os.getenv("DB_PASS")
    host = os.getenv("DB_HOST")
    port = os.getenv("DB_PORT")
    user = os.getenv("DB_USER")
    return connect(
        database=db_name, user=user, password=password, host=host, port=port
    )
