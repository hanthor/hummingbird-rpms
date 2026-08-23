import hashlib
import json
import sqlite3

assert hashlib.sha256(b"rpmtest").hexdigest().startswith("b28d")
assert json.loads('{"answer": 42}')["answer"] == 42
with sqlite3.connect(":memory:") as database:
    assert database.execute("select 40 + 2").fetchone() == (42,)
print("rpmtest Python works: 42")
