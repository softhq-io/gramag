"""One-time, idempotent migration for anonymous chat-history storage."""

from proto.chat_store import anonymize_chat_authors
from proto.db_proto import proto_db


def main() -> None:
    proto_db.connect()
    changed = anonymize_chat_authors()
    print(
        "Chat privacy migration complete: "
        f"sessions anonymized={changed['sessions']}, "
        f"messages anonymized={changed['messages']}"
    )


if __name__ == "__main__":
    main()
