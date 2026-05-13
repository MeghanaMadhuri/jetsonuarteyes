"""Guardrail: every FastAPI route in nina-link appears in LinkClient or companion Kotlin sources."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
API_FILE = REPO / "sirena_ui" / "android_gateway" / "fastapi_app.py"
DEPTH_STREAM_FILE = REPO / "sirena_ui" / "android_gateway" / "depth_stream.py"
LINK_CLIENT = (
    REPO
    / "android"
    / "app"
    / "src"
    / "main"
    / "java"
    / "com"
    / "sirena"
    / "nina"
    / "companion"
    / "data"
    / "LinkClient.kt"
)
COMPANION_KT_ROOT = (
    REPO
    / "android"
    / "app"
    / "src"
    / "main"
    / "java"
    / "com"
    / "sirena"
    / "nina"
    / "companion"
)


def _route_prefix_for_match(openapi_path: str) -> str:
    if "{" in openapi_path:
        return openapi_path.split("{", 1)[0].rstrip("/")
    return openapi_path


def _companion_kotlin_blob() -> str:
    parts = [LINK_CLIENT.read_text(encoding="utf-8")]
    if COMPANION_KT_ROOT.is_dir():
        for p in sorted(COMPANION_KT_ROOT.rglob("*.kt")):
            if p == LINK_CLIENT:
                continue
            parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


class TestLinkApiCompanionParity(unittest.TestCase):
    def test_companion_sources_cover_link_daemon_routes(self) -> None:
        if not API_FILE.is_file():
            self.skipTest("api.py not in tree")
        if not LINK_CLIENT.is_file():
            self.skipTest("LinkClient.kt not in tree")

        text = API_FILE.read_text(encoding="utf-8")
        routes = re.findall(r'@app\.(get|post|put|delete|patch)\(\s*"([^"]+)"', text)
        self.assertTrue(routes, "expected @app.* route decorators in fastapi_app.py")

        blob = _companion_kotlin_blob()
        missing: list[tuple[str, str]] = []
        for method, path in routes:
            needle = _route_prefix_for_match(path)
            if needle not in blob and path not in blob:
                missing.append((method.upper(), path))

        self.assertFalse(
            missing,
            "Add LinkClient (or companion .kt URL) coverage for:\n"
            + "\n".join(f"  {m} {p}" for m, p in missing),
        )

    def test_vision_status_reports_real_toggle_state(self) -> None:
        text = API_FILE.read_text(encoding="utf-8")
        self.assertIn('"face_enabled": st.face_enabled', text)
        self.assertIn('"object_enabled": st.object_enabled', text)
        self.assertIn('"object_confidence": float(gw.service.vision.get_object_confidence())', text)
        self.assertIn('"fps": round(fps_val, 2)', text)
        self.assertNotIn('"face_enabled": True', text)
        self.assertNotIn('"object_enabled": True', text)

    def test_depth_status_includes_machine_readable_code(self) -> None:
        text = DEPTH_STREAM_FILE.read_text(encoding="utf-8")
        self.assertIn('"code"', text)
        self.assertIn('"ready"', text)


if __name__ == "__main__":
    unittest.main()
