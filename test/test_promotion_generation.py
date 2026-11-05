"""Tests for promotion pipeline generation."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "generate_resources",
    Path(__file__).resolve().parent.parent / "ci" / "generate_resources.py",
)
generate_resources = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generate_resources)


class TestPromotionGeneration(unittest.TestCase):
    def test_promoted_import_counts_before_pending_package(self):
        rendered = generate_resources.render_template(
            generate_resources.ROOT_DIR / ".tekton" / "rpms-on-push.yaml.j2",
            generate_resources.ROOT_DIR / ".tekton" / "macros",
            {
                "rpms": [
                    {"dname": "alpha", "imported": True, "push_pipeline": "promotion"},
                    {"dname": "beta", "imported": False},
                ]
            },
        )

        self.assertIn("# --- beta ---\n# PENDING", rendered)
        self.assertNotIn("# --- alpha ---", rendered)

    def test_invalid_push_pipeline_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "rpms" / "alpha").mkdir(parents=True)
            (root / "ci").mkdir()
            (root / "ci" / "package-overrides.yaml").write_text(
                "alpha:\n  push_pipeline: invalid\n"
            )

            with mock.patch.object(generate_resources, "ROOT_DIR", root):
                with self.assertRaisesRegex(
                    ValueError, "push_pipeline must be build or promotion"
                ):
                    generate_resources.build_pac_variables(
                        "main", "hummingbird-tenant", "push"
                    )
