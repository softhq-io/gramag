"""Gramag — Seed default users into FalkorDB."""

from db import db
from auth import hash_password

USERS = [
    {"username": "admin", "password": "PoTPbaK8RQkkZjBlNFJJ", "role": "dispatcher", "name": "Admin"},
    {"username": "techniker", "password": "techniker", "role": "technician", "name": "Techniker"},
]


def seed():
    db.connect()
    for u in USERS:
        pw_hash = hash_password(u["password"])
        db.write(
            "MERGE (u:User {username: $username}) "
            "SET u.password_hash = $hash, u.role = $role, u.name = $name",
            {"username": u["username"], "hash": pw_hash, "role": u["role"], "name": u["name"]},
        )
        print(f"  + User {u['username']} ({u['role']})")
    print("Done — default users seeded.")


if __name__ == "__main__":
    seed()
