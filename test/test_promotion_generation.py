"""Tests for promotion pipeline generation."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

SPEC = importlib.util.spec_from_file_location(
    "generate_resources",
    Path(__file__).resolve().parent.parent / "ci" / "generate_resources.py",
)
assert SPEC is not None and SPEC.loader is not None
generate_resources = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generate_resources)


class TestPromotionGeneration(unittest.TestCase):
    def test_generated_promotion_pipelines_remain_separate_documents(self):
        root = Path(__file__).resolve().parent.parent
        documents = list(yaml.safe_load_all((root / ".tekton/rpms-on-promotion.yaml").read_text()))
        names = [document["metadata"]["name"] for document in documents]
        push = [
            document
            for document in yaml.safe_load_all((root / ".tekton/rpms-on-push.yaml").read_text())
            if document is not None
        ]
        packages = {entry.name for entry in (root / "rpms").iterdir() if entry.is_dir()}
        overrides = yaml.safe_load((root / "ci/package-overrides.yaml").read_text())
        build_exceptions = {
            package for package in packages
            if overrides.get(package, {}).get("push_pipeline") == "build"
        }

        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(
            set(names),
            {
                f"{generate_resources.sanitize_component_name(package)}-main-on-promotion"
                for package in packages - build_exceptions
            },
        )
        self.assertEqual(
            {document["metadata"]["name"] for document in push},
            {
                f"{generate_resources.sanitize_component_name(package)}-main-on-push"
                for package in build_exceptions
            },
        )
        self.assertIn("gorget-main-on-promotion", names)
        self.assertIn("syft-main-on-promotion", names)
        self.assertNotIn("name: gorget-main-on-push", (root / ".tekton/rpms-on-push.yaml").read_text())
        self.assertIn("name: gorget-main-on-pull-request", (root / ".tekton/rpms-on-pull-request.yaml").read_text())
        # These tests cover the shared promotion path; package rollouts do not need individual assertions.
        for document in documents:
            with self.subTest(pipeline=document["metadata"]["name"]):
                self.assertEqual(
                    document["spec"]["workspaces"],
                    [{"name": "git-auth", "secret": {"secretName": "{{git_auth_secret}}"}}],
                )
                self.assertEqual(
                    document["spec"]["pipelineSpec"]["workspaces"],
                    [{"name": "git-auth"}],
                )
                clone = document["spec"]["pipelineSpec"]["tasks"][0]
                self.assertEqual(clone["name"], "clone-repository")
                self.assertEqual(
                    clone["workspaces"],
                    [{"name": "basic-auth", "workspace": "git-auth"}],
                )

    def test_push_template_omits_promoted_and_pending_packages(self):
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

        self.assertEqual(
            rendered.strip(),
            "# Generated push-build PipelineRuns for explicit push_pipeline: build exceptions.",
        )

    def test_promotion_is_default_and_build_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "rpms" / "alpha").mkdir(parents=True)
            (root / "rpms" / "beta").mkdir()
            (root / "ci").mkdir()
            (root / "ci" / "package-overrides.yaml").write_text(
                "beta:\n  push_pipeline: build\n"
            )

            with mock.patch.object(generate_resources, "ROOT_DIR", root):
                variables = generate_resources.build_pac_variables(
                    "main", "hummingbird-tenant", "push"
                )

            self.assertEqual(
                {rpm["dname"]: rpm["push_pipeline"] for rpm in variables["rpms"]},
                {"alpha": "promotion", "beta": "build"},
            )

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
