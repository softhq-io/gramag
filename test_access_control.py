"""Adversarial authorization and machine-isolation tests."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException, Response
from fastapi.routing import APIRoute
from starlette.requests import Request

import auth
import auth_router
import authorization
import manage_users
import user_service
import proto_server
from proto import chat_store
from proto import retriever as proto_retriever
from proto import router as proto_router


class QueryResult:
    def __init__(self, columns: list[str] = None, rows: list[list] = None):
        columns = columns or []
        self.header = [[1, column] for column in columns]
        self.result_set = rows or []


def principal(role: str = "user", clients: list[str] | None = None) -> dict:
    return {
        "id": "user_a",
        "email": "a@example.com",
        "username": None,
        "identifier": "a@example.com",
        "name": "A",
        "role": role,
        "active": True,
        "must_change_password": False,
        "auth_version": 3,
        "all_clients": role in {"superadmin", "all_clients"},
        "client_ids": clients or [],
    }


class TokenLifecycleTests(unittest.TestCase):
    def test_auth_version_change_revokes_access_token(self):
        user = principal("user", ["client_a"])
        token = auth.create_access_token(user)
        changed = {**user, "auth_version": 4}
        with patch.object(auth, "load_user", return_value=changed):
            with self.assertRaises(HTTPException) as ctx:
                auth._validated_token_user(token, "access")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_deactivated_user_cannot_refresh(self):
        user = principal("all_clients")
        token = auth.create_refresh_token(user)
        with patch.object(auth, "load_user", return_value={**user, "active": False}):
            with self.assertRaises(HTTPException) as ctx:
                auth._validated_token_user(token, "refresh")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_all_clients_role_does_not_grant_user_admin(self):
        with self.assertRaises(HTTPException) as ctx:
            auth.require_superadmin(principal("all_clients"))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_password_change_token_cannot_be_used_as_access_token(self):
        user = {**principal("user", ["client_a"]), "must_change_password": True}
        token = auth.create_password_change_token(user)
        with patch.object(auth, "load_user", return_value=user):
            with self.assertRaises(HTTPException) as ctx:
                auth._validated_token_user(token, "access")
        self.assertEqual(ctx.exception.status_code, 401)


class LoginCooldownTests(unittest.TestCase):
    def setUp(self):
        auth_router._attempts.clear()
        self.request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/auth/login",
                "headers": [],
                "client": ("127.0.0.1", 12345),
            }
        )

    def test_locked_account_returns_retry_after(self):
        locked_until = (
            datetime.now(timezone.utc) + timedelta(minutes=5)
        ).isoformat()
        user = {
            **principal("all_clients"),
            "password_hash": "unused",
            "locked_until": locked_until,
            "failed_login_count": 5,
        }
        with patch.object(auth_router, "load_user", return_value=user):
            with self.assertRaises(HTTPException) as ctx:
                auth_router.login(
                    auth_router.LoginRequest(email="admin", password="correct"),
                    self.request,
                    Response(),
                )
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(ctx.exception.detail["code"], "login_cooldown")
        self.assertGreater(ctx.exception.detail["retry_after"], 0)
        self.assertEqual(
            ctx.exception.headers["Retry-After"],
            str(ctx.exception.detail["retry_after"]),
        )

    def test_fifth_failure_starts_cooldown_immediately(self):
        user = {
            **principal("all_clients"),
            "password_hash": "hash",
            "locked_until": None,
            "failed_login_count": 4,
        }
        with patch.object(auth_router, "load_user", return_value=user), \
             patch.object(auth_router, "verify_password", return_value=False), \
             patch.object(auth_router.db, "write") as write:
            with self.assertRaises(HTTPException) as ctx:
                auth_router.login(
                    auth_router.LoginRequest(email="admin", password="wrong"),
                    self.request,
                    Response(),
                )
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(
            ctx.exception.detail["retry_after"],
            auth_router.LOGIN_COOLDOWN_SECONDS,
        )
        params = write.call_args.args[1]
        self.assertEqual(params["failures"], auth_router.LOGIN_FAILURE_LIMIT)
        self.assertIsNotNone(params["locked_until"])

    def test_expired_cooldown_starts_a_fresh_attempt_count(self):
        user = {
            **principal("all_clients"),
            "locked_until": (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat(),
            "failed_login_count": 5,
        }
        with patch.object(auth_router.db, "write") as write:
            retry_after = auth_router._record_failure(user)
        self.assertEqual(retry_after, 0)
        params = write.call_args.args[1]
        self.assertEqual(params["failures"], 1)
        self.assertIsNone(params["locked_until"])


class UserLifecycleTests(unittest.TestCase):
    def test_generated_temporary_password_is_strong(self):
        password = user_service.generate_temporary_password()
        self.assertGreaterEqual(len(password), 20)

    def test_username_is_normalized_and_validated(self):
        self.assertEqual(user_service.validate_username(" Field.Tech "), "field.tech")
        for invalid in ("ab", "bad name", "name@example.com", "_starts-wrong"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(HTTPException) as ctx:
                    user_service.validate_username(invalid)
                self.assertEqual(ctx.exception.status_code, 422)

    def test_user_can_be_created_with_username_instead_of_email(self):
        created_user = {
            **principal("all_clients"),
            "id": "user_fixed",
            "email": None,
            "username": "field.tech",
            "identifier": "field.tech",
            "name": "Field Tech",
            "client_ids": [],
        }
        with patch.object(user_service.db, "query"), \
             patch.object(user_service.db, "write") as write, \
             patch.object(user_service, "result_single", side_effect=[
                 None, {"id": "user_fixed"},
             ]), \
             patch.object(user_service.uuid, "uuid4", return_value=SimpleNamespace(hex="fixed")), \
             patch.object(user_service, "generate_temporary_password", return_value="temporary-password"), \
             patch.object(user_service, "hash_password", return_value="password-hash"), \
             patch.object(user_service, "load_user", return_value=created_user):
            user, password = user_service.create_user(
                username="Field.Tech",
                name="Field Tech",
                role="all_clients",
                client_ids=[],
                actor_id="admin",
            )
        create_params = write.call_args_list[0].args[1]
        self.assertEqual(create_params["identifier"], "field.tech")
        self.assertEqual(create_params["username"], "field.tech")
        self.assertIsNone(create_params["email"])
        self.assertEqual(user["identifier"], "field.tech")
        self.assertEqual(password, "temporary-password")

    def test_last_superadmin_cannot_be_deactivated(self):
        current = principal("superadmin")
        with patch.object(user_service, "load_user", return_value=current), \
             patch.object(user_service, "_active_superadmin_count", return_value=0):
            with self.assertRaises(HTTPException) as ctx:
                user_service.update_user(
                    current["id"], {"active": False}, actor_id=current["id"]
                )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_user_cannot_delete_own_account(self):
        current = principal("superadmin")
        with patch.object(user_service, "load_user", return_value=current):
            with self.assertRaises(HTTPException) as ctx:
                user_service.delete_user(current["id"], actor_id=current["id"])
        self.assertEqual(ctx.exception.status_code, 409)

    def test_last_superadmin_cannot_be_deleted(self):
        target = {**principal("superadmin"), "id": "user_b"}
        with patch.object(user_service, "load_user", return_value=target), \
             patch.object(user_service.db, "write", return_value=QueryResult()) as write, \
             patch.object(chat_store, "anonymize_deleted_user") as anonymize:
            with self.assertRaises(HTTPException) as ctx:
                user_service.delete_user(target["id"], actor_id="user_a")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("other_active_superadmins", write.call_args.args[0])
        anonymize.assert_not_called()

    def test_delete_user_scrubs_chats_and_removes_account_atomically(self):
        target = {**principal("user", ["client_a"]), "id": "user_b"}
        writes = [
            QueryResult(["id"], [["user_b"]]),
            QueryResult(["deleted"], [[True]]),
        ]
        with patch.object(user_service, "load_user", return_value=target), \
             patch.object(chat_store, "anonymize_deleted_user") as anonymize, \
             patch.object(user_service.db, "write", side_effect=writes) as write:
            user_service.delete_user(target["id"], actor_id="user_a")
        anonymize.assert_called_once_with("user_b")
        reserve_cypher = write.call_args_list[0].args[0]
        delete_cypher, params = write.call_args_list[1].args
        self.assertIn("other_active_superadmins", reserve_cypher)
        self.assertIn("u.active = false", reserve_cypher)
        self.assertIn("DETACH DELETE u", delete_cypher)
        self.assertIn("action: 'user_deleted'", delete_cypher)
        self.assertEqual(params["details"], '{"role": "user"}')
        self.assertNotIn("a@example.com", str(params))

    def test_regular_user_assignments_are_validated(self):
        current = principal("user", ["client_a"])
        with patch.object(user_service, "load_user", return_value=current), \
             patch.object(user_service, "_validate_clients", side_effect=HTTPException(422, "Unknown")):
            with self.assertRaises(HTTPException) as ctx:
                user_service.update_user(
                    current["id"], {"client_ids": ["client_b"]}, actor_id="admin"
                )
        self.assertEqual(ctx.exception.status_code, 422)

    def test_user_listing_sorts_after_aggregated_query(self):
        columns = [
            "id", "email", "username", "identifier", "name", "role",
            "active", "must_change_password",
            "created_at", "updated_at", "last_login_at", "client_ids",
        ]
        result = QueryResult(columns, [
            [
                "u2", "z@example.com", None, "z@example.com", "Zulu",
                "user", True, False, None, None, None, [None],
            ],
            [
                "u1", None, "alpha", "alpha", "alpha", "all_clients",
                True, False, None, None, None, [],
            ],
        ])
        captured = {}

        def query(cypher, params=None):
            captured["cypher"] = cypher
            return result

        with patch.object(user_service.db, "query", side_effect=query):
            users = user_service.list_users()
        self.assertEqual([user["id"] for user in users], ["u1", "u2"])
        self.assertEqual(users[1]["client_ids"], [])
        self.assertNotIn("ORDER BY", captured["cypher"])

    def test_legacy_users_keep_password_hashes_and_receive_all_clients(self):
        legacy_users = QueryResult(
            ["id", "username", "email", "email_normalized", "role", "password_hash"],
            [
                [None, "admin", None, None, "dispatcher", "hash-admin"],
                [None, "techniker", None, None, "technician", "hash-techniker"],
            ],
        )
        current_emails = QueryResult(["email_normalized"], [])
        with patch.object(manage_users.db, "query", side_effect=[legacy_users, current_emails]):
            plan = manage_users._legacy_migration_plan({})
        self.assertEqual([item["username"] for item in plan], ["admin", "techniker"])
        self.assertEqual({item["email"] for item in plan}, {
            "admin@legacy.invalid", "techniker@legacy.invalid"
        })
        self.assertTrue(all("password_hash" not in item for item in plan))

    def test_legacy_migration_never_sets_or_transmits_password_hash(self):
        plan = [{
            "id": "legacy_a", "username": "admin", "email": "admin@legacy.invalid",
            "previous_role": "dispatcher", "audit_id": "audit_a",
        }]
        captured = {}

        def write(cypher, params=None):
            captured["cypher"] = cypher
            captured["params"] = params
            return QueryResult(["migrated"], [[1]])

        with patch.object(manage_users.db, "connect"), \
             patch.object(manage_users.db, "query", return_value=QueryResult(["count"], [[1]])), \
             patch.object(manage_users, "_legacy_migration_plan", return_value=plan), \
             patch.object(manage_users.db, "write", side_effect=write), \
             patch("proto.db_proto.proto_db.write"):
            manage_users._migrate_legacy(dry_run=False, email_mappings={})
        self.assertNotIn("SET u.password_hash", captured["cypher"])
        self.assertNotIn("hash-admin", str(captured["params"]))
        self.assertIn("u.role = 'all_clients'", captured["cypher"])
        self.assertIn("u.must_change_password = false", captured["cypher"])


class ResourceIsolationTests(unittest.TestCase):
    def test_guessed_erp_machine_id_is_hidden(self):
        user = principal("user", ["client_a"])
        with patch.object(authorization.db, "query", return_value=QueryResult()):
            with self.assertRaises(HTTPException) as ctx:
                authorization.require_erp_machine(user, "client_b_machine")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_guessed_document_id_is_hidden(self):
        user = principal("user", ["client_a"])
        with patch.object(authorization.proto_db, "query", return_value=QueryResult()):
            with self.assertRaises(HTTPException) as ctx:
                authorization.require_proto_document(user, "client_b_document")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_resource_queries_receive_only_current_grants(self):
        captured = {}

        def query(cypher, params):
            captured["cypher"] = cypher
            captured["params"] = params
            return QueryResult()

        with patch.object(authorization.proto_db, "query", side_effect=query):
            with self.assertRaises(HTTPException):
                authorization.require_proto_section(principal("user", ["client_a"]), "section_b")
        self.assertFalse(captured["params"]["all_clients"])
        self.assertEqual(captured["params"]["client_ids"], ["client_a"])
        self.assertIn("m.erp_customer_id IN $client_ids", captured["cypher"])


class RetrievalIsolationTests(unittest.TestCase):
    def test_vector_search_uses_exact_machine_and_client_scope(self):
        captured = {}

        def query(cypher, params):
            captured["cypher"] = cypher
            captured["params"] = params
            return QueryResult()

        with patch.object(proto_retriever.proto_db, "query", side_effect=query):
            results = proto_retriever._vector_search(
                "ManualSection",
                [0.0, 1.0],
                6,
                "machine_x",
                None,
                False,
                ["client_a"],
            )
        self.assertEqual(results, [])
        self.assertEqual(captured["params"]["slug"], "machine_x")
        self.assertEqual(captured["params"]["client_ids"], ["client_a"])
        self.assertIn("m.slug = $slug", captured["cypher"])
        self.assertIn("m.erp_customer_id IN $client_ids", captured["cypher"])

    def test_no_machine_chat_does_not_search_cross_machine_memory(self):
        with patch.object(chat_store, "generate_query_embedding") as embedding:
            result = chat_store.retrieve_memory(
                query="fault",
                session={"id": "chat_1", "machine_slug": None},
                user=principal("user", ["client_a"]),
            )
        self.assertEqual(result, [])
        embedding.assert_not_called()

    def test_machine_chat_memory_has_exact_machine_predicate(self):
        captured = {}

        def query(cypher, params):
            captured["cypher"] = cypher
            captured["params"] = params
            return QueryResult()

        with patch.object(chat_store, "generate_query_embedding", return_value=[0.0, 1.0]), \
             patch.object(chat_store.proto_db, "query", side_effect=query):
            chat_store.retrieve_memory(
                query="fault",
                session={"id": "chat_1", "machine_slug": "machine_x"},
                user=principal("user", ["client_a"]),
            )
        self.assertEqual(captured["params"]["machine_slug"], "machine_x")
        self.assertIn("s.machine_slug = $machine_slug", captured["cypher"])
        self.assertIn("s.created_by_id = $user_id", captured["cypher"])
        self.assertNotIn("msg.username", captured["cypher"])
        self.assertNotIn("s.customer =", captured["cypher"])


class ChatPrivacyTests(unittest.TestCase):
    def test_client_chat_listing_requires_ownership_and_machine_grant(self):
        captured = {}

        def query(cypher, params):
            captured["cypher"] = cypher
            captured["params"] = params
            return QueryResult()

        with patch.object(chat_store.proto_db, "query", side_effect=query):
            chat_store.list_sessions(
                machine_slug="machine_x",
                customer="Client A",
                user=principal("user", ["client_a"]),
            )
        self.assertIn("s.created_by_id = $user_id", captured["cypher"])
        self.assertIn("m.erp_customer_id IN $client_ids", captured["cypher"])
        self.assertIn("s.machine_slug IS NULL", captured["cypher"])
        self.assertNotIn("OR m.slug IS NULL", captured["cypher"])
        self.assertFalse(captured["params"]["all_clients"])

    def test_chat_authorization_is_owner_scoped_for_client_users(self):
        captured = {}

        def query(cypher, params):
            captured["cypher"] = cypher
            captured["params"] = params
            return QueryResult()

        with patch.object(authorization.proto_db, "query", side_effect=query):
            with self.assertRaises(HTTPException) as ctx:
                authorization.require_proto_chat(
                    principal("user", ["client_a"]),
                    "someone_elses_chat",
                )
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("s.created_by_id = $user_id", captured["cypher"])
        self.assertIn("s.machine_slug IS NULL", captured["cypher"])
        self.assertNotIn("OR m.slug IS NULL", captured["cypher"])
        self.assertEqual(captured["params"]["user_id"], "user_a")

    def test_technical_user_can_read_anonymous_cross_user_chat(self):
        result = QueryResult(
            [
                "id", "machine_slug", "client_id", "customer", "title",
                "created_at", "updated_at", "created_by_id", "owned_by_me",
                "isolation_version", "message_count", "last_message_at",
            ],
            [[
                "chat_b", "machine_x", "client_a", "Client A", "Fault",
                "now", "now", "user_b", False, 3, 2, "now",
            ]],
        )
        with patch.object(authorization.proto_db, "query", return_value=result):
            chat = authorization.require_proto_chat(principal("all_clients"), "chat_b")
        self.assertFalse(chat["owned_by_me"])
        self.assertNotIn("created_by", chat)

    def test_technical_user_cannot_append_to_another_users_chat(self):
        session = {
            "id": "chat_b",
            "machine_slug": "machine_x",
            "owned_by_me": False,
            "isolation_version": 3,
        }
        with patch.object(proto_router, "require_proto_chat", return_value=session), \
             patch.object(proto_router, "append_message") as append:
            with self.assertRaises(HTTPException) as ctx:
                proto_router.chat_message(
                    "chat_b",
                    proto_router.ProtoChatMessageRequest(text="test"),
                    current_user=principal("all_clients"),
                )
        self.assertEqual(ctx.exception.status_code, 403)
        append.assert_not_called()

    def test_public_messages_never_return_identity_fields(self):
        result = QueryResult(
            ["id", "session_id", "role", "text", "created_at", "model", "citations_json", "hits_json"],
            [["msg_1", "chat_1", "user", "fault", "now", None, "[]", "[]"]],
        )
        with patch.object(chat_store.proto_db, "query", return_value=result):
            message = chat_store.list_messages("chat_1")[0]
        self.assertNotIn("username", message)
        self.assertNotIn("user_role", message)

    def test_new_sessions_store_only_opaque_owner_id(self):
        captured = []

        def write(cypher, params=None):
            captured.append((cypher, params or {}))
            return QueryResult()

        with patch.object(chat_store.proto_db, "write", side_effect=write), \
             patch.object(chat_store, "get_session", return_value={"id": "chat_fixed", "owned_by_me": True}), \
             patch.object(chat_store, "_new_id", return_value="chat_fixed"):
            chat_store.create_session(
                machine_slug=None,
                customer=None,
                client_id=None,
                title="Test",
                user=principal("user", ["client_a"]),
            )
        cypher, params = captured[0]
        self.assertNotIn("created_by:", cypher)
        self.assertNotIn("username", params)
        self.assertIn("isolation_version: 3", cypher)

    def test_anonymization_migration_is_idempotent_and_preserves_content(self):
        results = [
            QueryResult(["changed"], [[2]]),
            QueryResult(["changed"], [[4]]),
            QueryResult(["changed"], [[0]]),
            QueryResult(["changed"], [[0]]),
        ]
        with patch.object(chat_store.proto_db, "write", side_effect=results) as write:
            first = chat_store.anonymize_chat_authors()
            second = chat_store.anonymize_chat_authors()
        self.assertEqual(first, {"sessions": 2, "messages": 4})
        self.assertEqual(second, {"sessions": 0, "messages": 0})
        cypher = "\n".join(call.args[0] for call in write.call_args_list)
        self.assertIn("REMOVE s.created_by", cypher)
        self.assertIn("REMOVE msg.username, msg.user_role", cypher)
        self.assertNotIn("DELETE msg", cypher)

    def test_deleted_user_chat_anonymization_removes_owner_link(self):
        with patch.object(chat_store.proto_db, "write", return_value=QueryResult()) as write:
            chat_store.anonymize_deleted_user("user_b")
        self.assertEqual(write.call_count, 2)
        messages_cypher = write.call_args_list[0].args[0]
        session_cypher = write.call_args_list[1].args[0]
        self.assertIn("REMOVE msg.username, msg.user_role", messages_cypher)
        self.assertIn("REMOVE s.created_by, s.created_by_id", session_cypher)

    def test_privacy_migration_failure_blocks_startup(self):
        with patch.object(proto_server.proto_db, "connect"), \
             patch.object(chat_store, "anonymize_chat_authors", side_effect=RuntimeError("boom")):
            with self.assertRaisesRegex(RuntimeError, "refusing to start"):
                proto_server.startup()


class RouteProtectionTests(unittest.TestCase):
    def test_every_business_api_route_has_an_auth_dependency(self):
        anonymous = {
            "/api/auth/login",
            "/api/auth/change-password",
            "/api/auth/refresh",
            "/api/auth/logout",
        }
        for route in proto_server.app.routes:
            if not isinstance(route, APIRoute) or not route.path.startswith("/api"):
                continue
            if route.path in anonymous:
                continue
            self.assertTrue(route.dependant.dependencies, route.path)

    def test_cross_machine_similar_cases_route_is_removed(self):
        paths = {route.path for route in proto_server.app.routes if isinstance(route, APIRoute)}
        self.assertNotIn("/api/mission/machine/{erp_id}/similar-cases", paths)


if __name__ == "__main__":
    unittest.main()
