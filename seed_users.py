"""Deprecated compatibility shim; users are managed with manage_users.py."""


def seed():
    print("User seeding is disabled. Run `python manage_users.py bootstrap --email ...` once.")


if __name__ == "__main__":
    seed()
