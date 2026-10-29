"""Phase 5 item 7: security review checks for the authenticated API surface."""

import re
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from voice_harness.api import create_app
from voice_harness.contracts import RunConfig
from voice_harness.engine import Engine
from voice_harness.storage import Database

ALLOWED_ORIGIN = "http://localhost:8765"
PUBLIC = {"/health", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
OPERATOR = {"Authorization": "Bearer op"}


class Base(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.database = Database(str(self.root / "t.sqlite"))
        self.app = create_app(self.database, self.root, "op", reviewer_token="rev")


class AuthSweepTests(Base):
    def test_every_api_route_rejects_a_missing_or_wrong_token(self):
        routes = [r for r in self.app.routes if isinstance(r, APIRoute) and r.path not in PUBLIC]
        self.assertGreater(len(routes), 40)
        with TestClient(self.app) as client:
            for route in routes:
                path = re.sub(r"\{[^}]+\}", "x", route.path)
                for method in route.methods - {"HEAD", "OPTIONS"}:
                    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "op"}):
                        response = client.request(method, path, headers=headers, json={})
                        self.assertEqual(response.status_code, 401, f"{method} {route.path} {headers}")

    def test_only_documented_routes_are_public(self):
        public = {r.path for r in self.app.routes if isinstance(r, APIRoute)
                  and not any(getattr(d.call, "__name__", "") == "auth" for d in r.dependant.dependencies)
                  and r.path not in PUBLIC}
        # Routers attach auth at router level; those routes still carry it in their dependant.
        self.assertEqual(public, set(), "routes without the auth dependency")

    def test_reviewer_cannot_write_anywhere_but_review_decisions(self):
        with TestClient(self.app) as client:
            writes = [r for r in self.app.routes if isinstance(r, APIRoute)
                      and r.path not in PUBLIC and r.methods & {"POST", "PUT", "PATCH", "DELETE"}]
            self.assertGreater(len(writes), 10)
            for route in writes:
                path = re.sub(r"\{[^}]+\}", "x", route.path)
                if re.fullmatch(r"/api/v1/(reviews/[A-Za-z0-9_-]+/(claim|decision)|calibration/policies/[0-9a-f]{64}/approve)", path):
                    continue
                for method in route.methods & {"POST", "PUT", "PATCH", "DELETE"}:
                    response = client.request(method, path, headers={"Authorization": "Bearer rev"}, json={})
                    self.assertEqual(response.status_code, 403, f"{method} {route.path}")


class OriginTests(Base):
    def test_foreign_origin_is_refused_even_with_a_valid_token(self):
        with TestClient(self.app) as client:
            for origin in ("http://evil.example", "https://localhost:8765", "null", "http://localhost:8765.evil.example"):
                response = client.get("/api/v1/providers", headers={**OPERATOR, "Origin": origin})
                self.assertEqual(response.status_code, 403, origin)
                self.assertEqual(response.json()["error"]["code"], "ORIGIN_DENIED")
            ok = client.get("/api/v1/providers", headers={**OPERATOR, "Origin": ALLOWED_ORIGIN})
            self.assertEqual(ok.status_code, 200)

    def test_no_cors_headers_are_ever_sent(self):
        with TestClient(self.app) as client:
            response = client.get("/api/v1/providers", headers={**OPERATOR, "Origin": ALLOWED_ORIGIN})
            self.assertFalse([h for h in response.headers if h.lower().startswith("access-control-")])
            preflight = client.options("/api/v1/runs", headers={"Origin": "http://evil.example",
                                                                "Access-Control-Request-Method": "POST"})
            self.assertNotIn("access-control-allow-origin", preflight.headers)


class WebSocketTests(Base):
    def setUp(self):
        super().setUp()
        self.run = Engine(self.database).create(RunConfig().model_dump())
        self.url = f"/api/v1/runs/{self.run}/ws"

    def refused(self, client, headers=None, subprotocols=None):
        with self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect(self.url, headers=headers or {}, subprotocols=subprotocols or ["harness.v1"]):
                pass

    def ticket(self, client, run=None):
        return client.post(f"/api/v1/runs/{run or self.run}/ws-ticket", headers=OPERATOR).json()["ticket"]

    def test_no_credentials_wrong_origin_and_missing_origin_are_refused(self):
        with TestClient(self.app) as client:
            self.refused(client, {"Origin": ALLOWED_ORIGIN})
            ticket = self.ticket(client)
            self.refused(client, {"Origin": "http://evil.example"}, ["harness.v1", "ticket." + ticket])
            self.refused(client, {}, ["harness.v1", "ticket." + self.ticket(client)])
            self.refused(client, {"Origin": ALLOWED_ORIGIN, "Authorization": "Bearer wrong"})

    def test_a_ticket_is_single_use_and_bound_to_its_run(self):
        other = Engine(self.database).create(RunConfig().model_dump())
        with TestClient(self.app) as client:
            wrong_run = self.ticket(client, other)
            self.refused(client, {"Origin": ALLOWED_ORIGIN}, ["harness.v1", "ticket." + wrong_run])
            self.refused(client, {"Origin": ALLOWED_ORIGIN}, ["harness.v1", "ticket." + wrong_run])
            used = self.ticket(client)
            self.refused(client, {"Origin": "http://evil.example"}, ["harness.v1", "ticket." + used])
            # A refused origin must still burn the ticket, so a leaked ticket cannot be retried.
            self.refused(client, {"Origin": ALLOWED_ORIGIN}, ["harness.v1", "ticket." + used])

    def test_an_expired_ticket_is_refused(self):
        with TestClient(self.app) as client:
            ticket = self.ticket(client)
            real = time.time
            try:
                time.time = lambda: real() + 31
                self.refused(client, {"Origin": ALLOWED_ORIGIN}, ["harness.v1", "ticket." + ticket])
            finally:
                time.time = real

    def test_the_ticket_pool_is_bounded(self):
        with TestClient(self.app) as client:
            codes = {client.post(f"/api/v1/runs/{self.run}/ws-ticket", headers=OPERATOR).status_code
                     for _ in range(1005)}
            self.assertEqual(codes, {200, 429})


class DownloadTests(Base):
    def test_export_filename_cannot_carry_header_or_path_injection(self):
        with TestClient(self.app) as client:
            for bad in ("x%22%0d%0aSet-Cookie:a=b", "..%2f..%2fetc%2fpasswd", "a%22;b"):
                response = client.get(f"/api/v1/bakeoffs/{bad}/report.html", headers=OPERATOR)
                self.assertEqual(response.status_code, 404, bad)
                self.assertNotIn("set-cookie", response.headers)

    def test_static_client_does_not_serve_outside_its_directory(self):
        with TestClient(self.app) as client:
            for path in ("/client/../src/voice_harness/api.py", "/client/%2e%2e/src/voice_harness/api.py",
                         "/client/..%2fsrc%2fvoice_harness%2fapi.py", "/client/%2e%2e%2f%2e%2e%2fCLAUDE.md"):
                response = client.get(path)
                self.assertNotEqual(response.status_code, 200, path)
                self.assertNotIn("create_app", response.text)

    def test_unknown_run_ids_do_not_reach_the_filesystem(self):
        with TestClient(self.app) as client:
            for bad in ("..%2f..%2fetc%2fpasswd", "%2e%2e", "x'%20OR%20'1'='1"):
                for path in ("/api/v1/runs/{}", "/api/v1/runs/{}/events", "/api/v1/runs/{}/evidence"):
                    response = client.get(path.format(bad), headers=OPERATOR)
                    self.assertIn(response.status_code, {404, 422}, path.format(bad))


class SecretsTests(Base):
    def test_token_files_are_private_and_distinct(self):
        create_app(Database(str(self.root / "g.sqlite")), self.root)  # no explicit token: generates the files
        for name in ("operator-token", "reviewer-token"):
            self.assertEqual((self.root / name).stat().st_mode & 0o777, 0o600, name)
        self.assertNotEqual((self.root / "operator-token").read_text(), (self.root / "reviewer-token").read_text())

    def test_error_bodies_and_headers_never_echo_the_token(self):
        with TestClient(self.app) as client:
            response = client.get("/api/v1/runs/missing", headers={"Authorization": "Bearer op-but-wrong"})
            self.assertNotIn("op-but-wrong", response.text + str(response.headers))
            response = client.get("/api/v1/runs/missing", headers=OPERATOR)
            self.assertNotIn("op", response.json()["error"]["message"].lower().split("_"))

    def test_html_pages_carry_a_strict_csp_without_inline_script(self):
        pages = sorted((Path(__file__).resolve().parents[1] / "apps" / "web").glob("*.html"))
        self.assertGreater(len(pages), 5)
        for page in pages:
            text = page.read_text()
            csp = re.search(r'Content-Security-Policy"\s+content="([^"]+)"', text)
            self.assertIsNotNone(csp, page.name)
            self.assertIn("default-src 'none'", csp.group(1), page.name)
            self.assertNotIn("unsafe-inline", re.search(r"script-src[^;]*", csp.group(1)).group(0), page.name)
            self.assertNotIn("unsafe-eval", csp.group(1), page.name)
            self.assertFalse(re.search(r"<script(?![^>]*\bsrc=)[^>]*>\s*\S", text), page.name + ": inline script")


if __name__ == "__main__":
    unittest.main()
