from sqlite3 import connect
import sqlite3

def open_db():
    a = connect(database='local.db', host='x')
    b = sqlite3.connect(database='other.db')
    return a, b
