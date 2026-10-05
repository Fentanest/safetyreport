"""Render the actual file browser route and check query-selected tab semantics."""
from html.parser import HTMLParser
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
from web.routers import file_browser_route as route


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.by_id = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.by_id[attrs["id"]] = attrs


class FileBrowserTargetTests(unittest.TestCase):
    def test_target_selects_only_requested_tab_and_panel(self):
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="file-browser-fixture")
        app.include_router(route.router)
        with TestClient(app) as client, mock.patch.object(
            route.file_service, "list_browser_groups", return_value={"logs": [], "results": []}
        ):
            for query, selected in (("", "results"), ("?target=logs", "logs"),
                                    ("?target=results", "results"), ("?target=invalid", "results")):
                with self.subTest(query=query):
                    response = client.get("/file-browser" + query)
                    self.assertEqual(response.status_code, 200)
                    explicit = selected if query in ("?target=logs", "?target=results") else None
                    self.assertEqual(response.context["explicit_target"], explicit)
                    elements = Elements(response.text).by_id
                    for target in ("logs", "results"):
                        tab = elements[target + "-tab"]
                        panel = elements[target + "-pane"]
                        active = target == selected
                        self.assertEqual(tab["aria-selected"], str(active).lower())
                        self.assertEqual("active" in tab["class"].split(), active)
                        self.assertEqual("active" in panel["class"].split(), active)
                        self.assertEqual("show" in panel["class"].split(), active)
