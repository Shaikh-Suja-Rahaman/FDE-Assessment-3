"""Headless UI smoke tests (no LLM key needed: runs in deterministic degraded mode)."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from streamlit.testing.v1 import AppTest


class ReviewPageTests(unittest.TestCase):
    def test_analyze_existing_request_end_to_end(self):
        with mock.patch.dict(os.environ, {"GOOGLE_API_KEY": "", "GEMINI_API_KEY": ""}):
            at = AppTest.from_file("../app_pages/review.py", default_timeout=60).run()
            self.assertFalse(at.exception)
            at.selectbox(key="selected_request").set_value("REQ-1005").run()
            next(b for b in at.button if b.label == "Analyze request").click().run()
            self.assertFalse(at.exception)
            text = " ".join(m.value for m in at.markdown)
            self.assertIn("Route for specialist review", text)
            self.assertIn("Finance", text)
            self.assertIn("budget_insufficient", text)

    def test_evaluation_page_renders(self):
        at = AppTest.from_file("../app_pages/evaluation.py", default_timeout=30).run()
        self.assertFalse(at.exception)

    def test_decisions_page_renders(self):
        at = AppTest.from_file("../app_pages/decisions.py", default_timeout=30).run()
        self.assertFalse(at.exception)


if __name__ == "__main__":
    unittest.main()
