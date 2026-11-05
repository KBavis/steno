from mylib.db import query


def ask(sql: str):
    return query(sql)
