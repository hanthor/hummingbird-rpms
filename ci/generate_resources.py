#!/usr/bin/env python3
"""
Fast resource generator for Tekton/Konflux templates.

This replaces the shell scripts generate_pac_resources.sh and
generate_konflux_resources.sh with a Python implementation that
avoids spawning thousands of yq subprocesses.
"""

import argparse
import json
import logging
import re
import sys
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hum.meta import MetadataRepository  # noqa: E402
from hum.meta.model import (  # noqa: E402
    CleanPackageMetadata,
    IndependentPackageMetadata,
    MalformedMetadataError,
    ModifiedFedoraPackageMetadata,
    ModifiedUpstreamPackageMetadata,
)

import yaml  # noqa: E402
from jinja2 import Environment, FileSystemLoader  # noqa: E402

ROOT_DIR = Path(__file__).resolve().parent.parent

# Default configuration
DEFAULT_TIMEOUT_HOURS = 4
DEFAULT_BUILD_ARCHITECTURES = ["aarch64", "x86_64", "s390x"]
DEFAULT_BUILD_PLATFORMS = ["linux/x86_64", "linux/arm64", "linux/s390x"]
BUILD_TRIGGER_RPM_NAME = "setup"

# Konflux rpmbuild-pipeline uses one pipeline task per arch (not a single "rpmbuild" task).
# package-overrides may use pipelineTaskName: rpmbuild as shorthand for all default architectures.
RPMBUILD_PIPELINE_TASK_NAMES = ("rpmbuild-x86-64", "rpmbuild-aarch64", "rpmbuild-s390x")


def expand_task_run_specs(specs: list) -> list:
    """Expand shorthand task names in task_run_specs for generated PipelineRuns."""
    expanded: list = []
    for spec in specs:
        name = spec.get("pipelineTaskName")
        if name == "rpmbuild":
            for task_name in RPMBUILD_PIPELINE_TASK_NAMES:
                dup = dict(spec)
                dup["pipelineTaskName"] = task_name
                expanded.append(dup)
        else:
            expanded.append(spec)
    return expanded

# renovate: datasource=docker depName=quay.io/hummingbird-ci/rpmbuild-pipeline
PIPELINE_BUNDLE = "quay.io/hummingbird-ci/rpmbuild-pipeline:latest@sha256:119096893b0bcd007c6f243a6363b83216945b5a61dee77953b3d094bc2f7122"
# renovate: datasource=docker depName=quay.io/hummingbird-ci/hummingbird-tools
PROMOTION_TOOL_IMAGE = "quay.io/hummingbird-ci/hummingbird-tools:latest@sha256:8b96ded8dd73260216cc5f77d4e5193e0455e0adde056895264d610656c8c619"
PROMOTION_ARCHIVE_URL = "https://kubearchive-api-server-product-kubearchive.apps.kflux-prd-rh03.nnv1.p1.openshiftapps.com"

def load_yaml_file(path: Path) -> dict | None:
    """Load a YAML file, returning None if it doesn't exist."""
    if not path.exists():
        return None
    with open(path) as f:
        return yaml.safe_load(f)  # type: ignore[return-value]


def get_all_packages() -> list[str]:
    """Get sorted list of all package names from manifest and rpms/ directory."""
    packages = set()

    # From target-packages.yml
    manifest = load_yaml_file(ROOT_DIR / "target-packages.yml")
    if manifest and "packages" in manifest:
        packages.update(manifest["packages"])

    # From rpms/ directories
    rpms_dir = ROOT_DIR / "rpms"
    if rpms_dir.exists():
        for entry in rpms_dir.iterdir():
            if entry.is_dir():
                packages.add(entry.name)

    return sorted(packages)


def sanitize_component_name(name: str) -> str:
    """Sanitize package name for use as Kubernetes resource name."""
    # resource names must not contain underscores, uppercase, +, or .
    result = name.replace("_", "-").replace("+", "-").replace(".", "-")
    return result.lower()


def build_pac_variables(
    branch: str, tenant: str, resource_type: str, *,
    private_products: Mapping[str, str] | None = None,
) -> dict:
    """Build variables for PAC (Pipeline as Code) templates."""
    packages = get_all_packages()
    package_overrides = load_yaml_file(ROOT_DIR / "ci" / "package-overrides.yaml") or {}
    metadata_repo = MetadataRepository(ROOT_DIR)
    if private_products is None:
        private_products = metadata_repo.private_products()

    application_name = f"rpms-{branch}"

    rpms = []
    for dname in packages:
        imported = (ROOT_DIR / "rpms" / dname).is_dir()
        component_name = sanitize_component_name(dname)
        name = f"{component_name}-{branch}"

        rpm_data = {
            "name": name,
            "dname": dname,
            "imported": imported,
            "timeout_hours": DEFAULT_TIMEOUT_HOURS,
        }

        if imported:
            # Auto-detect spec file in package directory
            pkg_dir = ROOT_DIR / "rpms" / dname
            spec_files = list(pkg_dir.glob("*.spec"))
            if spec_files:
                rpm_data["specfile"] = spec_files[0].name
            else:
                # Fallback to default naming if no spec file found
                rpm_data["specfile"] = f"{dname}.spec"

            # Check for overrides
            pkg_config = package_overrides.get(dname, {})
            rpm_data["push_pipeline"] = pkg_config.get("push_pipeline", "promotion")
            if rpm_data["push_pipeline"] not in ("build", "promotion"):
                raise ValueError(f"{dname}: push_pipeline must be build or promotion")

            # Determine the upstream package name for Tekton pipeline
            #
            # For most packages, the directory name (dname) matches the package name.
            # However, for renamed packages (e.g., golang1.25, ruby3.3, tomcat10),
            # we need the upstream package name for lookaside cache and SRPM naming.
            #
            # When forked_from points to hummingbird, we use the directory name
            # since the hummingbird lookaside cache uses hummingbird package names.
            #
            # Independent packages (Hummingbird-specific) have no source URL in metadata,
            # so we use the directory name directly.
            forked_from = pkg_config.get("forked_from", "")
            is_hummingbird = "hummingbird" in forked_from

            try:
                metadata = metadata_repo.load(dname)
            except (OSError, json.JSONDecodeError, MalformedMetadataError) as e:
                logging.warning("%s: failed to load metadata, falling back to directory name: %s", dname, e)
                metadata = None

            if metadata is None or isinstance(metadata, IndependentPackageMetadata):
                upstream_name = dname
            elif is_hummingbird:
                upstream_name = dname
            elif isinstance(metadata, (CleanPackageMetadata, ModifiedFedoraPackageMetadata, ModifiedUpstreamPackageMetadata)):
                upstream_name = Path(metadata.fedora.git_url).stem
            else:
                upstream_name = dname

            # Store upstream package name for use in pipeline
            rpm_data["package_name"] = upstream_name

            if "timeout_hours" in pkg_config:
                rpm_data["timeout_hours"] = pkg_config["timeout_hours"]

            # Always include build targets, using overrides or defaults
            rpm_data["build_architectures"] = pkg_config.get(
                "build_architectures", DEFAULT_BUILD_ARCHITECTURES
            )
            rpm_data["build_platforms"] = pkg_config.get(
                "build_platforms", DEFAULT_BUILD_PLATFORMS
            )

            if "task_run_specs" in pkg_config:
                rpm_data["task_run_specs"] = expand_task_run_specs(
                    pkg_config["task_run_specs"]
                )

            if "forked_from" in pkg_config:
                rpm_data["forked_from"] = pkg_config["forked_from"]

            private_product = private_products.get(dname)
            if private_product:
                rpm_data["application_name"] = f"private-{private_product}-rpms-{branch}"

            extra_params = {"purl-rpm-namespace": "redhat"}
            if "extra_params" in pkg_config:
                extra_params.update(pkg_config["extra_params"])
            if resource_type == "pull-request":
                extra_params["image-expires-after"] = "5d"
            rpm_data["extra_params"] = extra_params

            if "lookaside_cache_url" in pkg_config:
                rpm_data["lookaside_cache_url"] = pkg_config["lookaside_cache_url"]

            # Trigger patterns (regex, used by both PAC CEL and trigger manifest).
            # Stored as standard regexes (single backslash escapes); the CEL
            # template doubles backslashes for CEL string literal syntax.
            escaped_dname = re.escape(dname)
            triggers = [f"^rpms/{escaped_dname}/"]
            if resource_type == "pull-request":
                triggers.append(rf"^test/rpms/{escaped_dname}\.yml$")
                if dname == BUILD_TRIGGER_RPM_NAME:
                    triggers.extend(["^ci/", "^mock/"])
            rpm_data["manifest_triggers"] = triggers

        rpms.append(rpm_data)

    return {
        "branch": branch,
        "application_name": application_name,
        "pipeline_bundle": PIPELINE_BUNDLE,
        "promotion_tool_image": PROMOTION_TOOL_IMAGE,
        "promotion_archive_url": PROMOTION_ARCHIVE_URL,
        "tenant": tenant,
        "rpms": rpms,
    }


# ITS trigger routing (D2): triggers that map to the PR manifest (with group flag)
# or to the push manifest.  Optional entries are silently excluded.
# NOTE: "component_attr-*" / "component_setup-*" are intentionally NOT listed —
# they are always optional=true in this repo and silently excluded before routing.
# The bare "component" value is reserved for future non-optional post-merge entries.
_KNOWN_ITS_TRIGGERS = {"pull_request", "component"}


def _load_its_config() -> dict:
    """Load konflux-templates/scenarios.yml — the single ITS config source (matrix format).

    Both the CR rendering (-> rendered.yml) and the trigger-manifest emission read
    this file.  Editing it changes both outputs atomically (D2 config-driven revision).
    """
    config_path = ROOT_DIR / "konflux-templates" / "scenarios.yml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def _render_its_scenarios() -> list[str]:
    """Render all ITS CR YAML documents from the scenario matrix and per-type templates.

    For each application × matrix entry, renders
    konflux-templates/scenarios/<template>.yml.j2 with application, triggers, and vars.
    When a suffix is present the template uses it for the CR name instead of the
    template name, allowing multiple instantiations of the same template per app.
    Returns a list of rendered YAML document strings (one per CR) in matrix order.
    Each string ends without a trailing newline so the caller controls spacing.
    """
    config = _load_its_config()
    env = Environment(
        loader=FileSystemLoader(str(ROOT_DIR / "konflux-templates" / "scenarios")),
    )
    rendered = []
    for app_name, entries in config["applications"].items():
        for entry in entries:
            tmpl_name = f"{entry['template']}.yml.j2"
            tmpl = env.get_template(tmpl_name)
            text = tmpl.render(
                application=app_name,
                template=entry["template"],
                suffix=entry.get("suffix", entry["template"]),
                triggers=entry["triggers"],
                optional=entry.get("optional", False),
                vars=entry.get("vars", {}),
            ).rstrip("\n")
            rendered.append(text)
    return rendered


def _route_its_entry(
    app_name: str, template: str, triggers: list[str], optional: bool
) -> tuple[bool, bool]:
    """Return (in_pr_manifest, in_push_manifest) for one matrix entry.

    pull_request  → PR list (group: true), not push list.
    component     → PR list (group: false), push list.
    optional      → excluded from both (silent).
    Anything else, including an empty trigger list → hard error (fail-closed, D2).
    """
    if optional:
        return False, False

    has_pull_request = "pull_request" in triggers
    has_component = "component" in triggers
    unknown = [t for t in triggers if t not in _KNOWN_ITS_TRIGGERS]
    if unknown:
        sys.exit(
            f"ERROR: matrix entry '{app_name}/{template}' has non-optional "
            f"trigger(s) not routable to the trigger manifest: {unknown}. "
            f"Mark the entry optional or extend the generator."
        )
    if not has_pull_request and not has_component:
        # Covers empty triggers [] and triggers containing only canary kinds
        # (component_attr-*, component_setup-*) — any non-optional entry that
        # contributes nothing to either manifest is a hard error.
        sys.exit(
            f"ERROR: matrix entry '{app_name}/{template}' is non-optional "
            f"but has no routable triggers (pull_request/component). "
            f"Mark the entry optional or add a routable trigger."
        )
    return True, has_component


def build_its_manifest_entries() -> dict[str, dict]:
    """Build integration_test_scenarios sections for PR and push manifests.

    Reads the matrix from scenarios.yml directly — the same config used to
    render rendered.yml — so the manifest cannot diverge from the deployed ITS
    (D2 config-driven).

    Returns {"pr": {app: [{"name": ..., "group": ...}]},
             "push": {app: [{"name": ...}]}}.
    Non-optional pull_request-trigger entries → PR list (group: true).
    Non-optional component-trigger entries → PR list (group: false) + push list.
    Optional entries are excluded from both lists.
    Generator exits non-zero for non-optional entries with unroutable triggers,
    including an empty trigger list.
    """
    config = _load_its_config()
    pr_manifest: dict[str, list] = {}
    push_manifest: dict[str, list] = {}

    for app_name, entries in config["applications"].items():
        for entry in entries:
            template = entry["template"]
            triggers = entry["triggers"]
            optional = entry.get("optional", False)
            name = f"{app_name}-{entry.get('suffix', template)}"
            in_pr, in_push = _route_its_entry(app_name, template, triggers, optional)
            if in_pr:
                pr_manifest.setdefault(app_name, []).append({
                    "name": name,
                    "group": "pull_request" in triggers,
                })
            if in_push:
                push_manifest.setdefault(app_name, []).append({"name": name})

    for app_name in pr_manifest:
        pr_manifest[app_name] = sorted(pr_manifest[app_name], key=lambda e: e["name"])
    for app_name in push_manifest:
        push_manifest[app_name] = sorted(push_manifest[app_name], key=lambda e: e["name"])

    return {"pr": pr_manifest, "push": push_manifest}


def build_konflux_variables(
    branch: str, tenant: str, git_repo: str, *,
    private_products: Mapping[str, str] | None = None,
) -> dict:
    """Build variables for Konflux templates."""
    packages = get_all_packages()
    if private_products is None:
        private_products = MetadataRepository(ROOT_DIR).private_products()

    application_name = f"rpms-{branch}"

    rpms = []
    for name in packages:
        imported = (ROOT_DIR / "rpms" / name).is_dir()
        component_name = sanitize_component_name(name)

        rpm_data = {
            "name": name,
            "imported": imported,
            "component_name": f"{component_name}-{branch}",
            "repository": name,
            "tags": ["latest"],
        }

        if imported:
            private_product = private_products.get(name)
            if private_product:
                rpm_data["application_name"] = f"private-{private_product}-rpms-{branch}"

            # Load properties.yml if it exists (only for imported packages)
            properties_file = ROOT_DIR / "rpms" / name / "properties.yml"
            if properties_file.exists():
                properties = load_yaml_file(properties_file)
                if properties:
                    rpm_data.update(properties)

        rpms.append(rpm_data)

    return {
        "application_name": application_name,
        "branch": branch,
        "tenant": tenant,
        "git_repo": git_repo,
        "integration_test_scenarios": _render_its_scenarios(),
        "rpms": rpms,
    }


_REQUIRED_RPA_FIELDS = [
    "name", "application_prefix", "release_org", "single_component_mode",
    "service_account_name", "pulp_unsigned_domain", "pulp_signed_domain",
    "pulp_secret_name", "pipeline_revision", "pipeline_url",
]


def build_releng_variables(
    rpa_config: dict, global_config: dict, *,
    private_products: Mapping[str, str] | None = None,
) -> dict:
    """Build variables for a single ReleasePlanAdmission."""
    rpa_name = rpa_config.get("name", "<unnamed>")
    for field in _REQUIRED_RPA_FIELDS:
        if field not in rpa_config:
            raise ValueError(
                f"RPA '{rpa_name}' is missing required field '{field}' "
                f"in ci/konflux_rpa_config.yml"
            )

    if private_products is None:
        private_products = MetadataRepository(ROOT_DIR).private_products()

    packages = get_all_packages()

    component_filter = rpa_config.get("component_filter", {})
    path_prefix = component_filter.get("path_prefix", "")
    if path_prefix:
        filter_dir = ROOT_DIR / path_prefix.rstrip("/")
        packages = [pkg for pkg in packages if (filter_dir / pkg).is_dir()]

    # Filter by private_product assignment
    # - Private RPAs (with private_product set): include only matching packages
    # - Non-private RPAs: automatically exclude packages that have a private_product
    private_product = rpa_config.get("private_product")

    if private_product:
        packages = [
            pkg for pkg in packages
            if private_products.get(pkg) == private_product
        ]
    else:
        packages = [
            pkg for pkg in packages
            if pkg not in private_products
        ]

    component_list = [
        {"component_name": f"{sanitize_component_name(pkg)}-{global_config['branch']}"}
        for pkg in packages
    ]

    return {
        "name": rpa_config["name"],
        "application_prefix": rpa_config["application_prefix"],
        "release_org": rpa_config["release_org"],
        "single_component_mode": rpa_config["single_component_mode"],
        "service_account_name": rpa_config["service_account_name"],
        "component_list": component_list,
        "release_tenant": global_config["release_tenant"],
        "branch": global_config["branch"],
        "tenant": global_config["tenant"],
        "pulp_unsigned_domain": rpa_config["pulp_unsigned_domain"],
        "pulp_signed_domain": rpa_config["pulp_signed_domain"],
        "pulp_secret_name": rpa_config["pulp_secret_name"],
        "pipeline_revision": rpa_config["pipeline_revision"],
        "pipeline_url": rpa_config["pipeline_url"],
    }


def load_releng_config() -> dict:
    """Load the releng RPA configuration file."""
    config_path = ROOT_DIR / "ci" / "konflux_rpa_config.yml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def generate_releng(*, private_products: Mapping[str, str] | None = None) -> dict[str, str]:
    """Generate releng (ReleasePlanAdmission) resources for all configured RPAs."""
    config = load_releng_config()
    global_config = config["global"]
    if private_products is None:
        private_products = MetadataRepository(ROOT_DIR).private_products()

    template_path = ROOT_DIR / "konflux-templates" / "releng-staging.yml.j2"
    macros_dir = ROOT_DIR / "konflux-templates" / "macros" / "releng"

    results = {}
    for rpa_config in config["rpas"]:
        print(f"Building releng variables for {rpa_config['name']}...", file=sys.stderr)
        variables = build_releng_variables(rpa_config, global_config, private_products=private_products)

        print(f"Rendering {rpa_config['name']}...", file=sys.stderr)
        result = render_template(template_path, macros_dir, variables)
        if not result.endswith("\n"):
            result += "\n"
        results[rpa_config["name"]] = result

    return results


def render_template(template_path: Path, macros_dir: Path, variables: dict) -> str:
    """Render a Jinja2 template with the given variables."""
    # Load macros and template
    macros_content = []
    if macros_dir.exists():
        for macro_file in (
            sorted(macros_dir.glob("*.j2"))
            if macros_dir.name == "macros"
            else sorted(macros_dir.glob("*.yml.j2"))
        ):
            macros_content.append(macro_file.read_text())

    template_content = template_path.read_text()
    full_template = "\n".join(macros_content) + "\n" + template_content

    # Create Jinja2 environment
    env = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
    )

    template = env.from_string(full_template)
    return template.render(**variables)


def generate_pac(resource_type: str, branch: str, tenant: str) -> str:
    """Generate PAC resources."""
    if resource_type not in ("push", "pull-request", "promotion"):
        sys.exit(f"ERROR: Invalid resource type: {resource_type}")

    print("Building template variables...", file=sys.stderr)
    variables = build_pac_variables(branch, tenant, "push" if resource_type == "promotion" else resource_type)

    print("Rendering Pipeline as Code resources...", file=sys.stderr)
    template_path = ROOT_DIR / ".tekton" / f"rpms-on-{resource_type}.yaml.j2"
    macros_dir = ROOT_DIR / ".tekton" / "macros"

    return render_template(template_path, macros_dir, variables).rstrip() + "\n"


def build_trigger_manifest(pac_variables: dict, event_type: str, its_entries: dict | None = None) -> dict:
    """Build trigger manifest from already-computed PAC variables.

    its_entries is the per-manifest slice from build_its_manifest_entries():
    {"app": [{"name": ..., "group": ...}]} for pull_request,
    {"app": [{"name": ...}]} for push.
    """
    default_application = pac_variables["application_name"]
    applications: dict[str, dict[str, list[str]]] = {}

    for rpm in pac_variables["rpms"]:
        if not rpm["imported"]:
            continue
        component_name = f"{sanitize_component_name(rpm['dname'])}--rpms"
        application = rpm.get("application_name", default_application)
        applications.setdefault(application, {})[component_name] = rpm[
            "manifest_triggers"
        ]

    manifest: dict = {
        "type": event_type,
        "excludes": [],
        "applications": applications,
    }
    if its_entries:
        manifest["integration_test_scenarios"] = its_entries
    return manifest


def generate_gitattributes() -> str:
    """Generate .gitattributes marking all generated files as gitlab-generated."""
    releng_config = load_releng_config()
    rpa_names = [rpa["name"] for rpa in releng_config["rpas"]]

    lines = [
        ".gitattributes gitlab-generated",
        ".tekton/rpms-on-pull-request.yaml gitlab-generated",
        ".tekton/rpms-on-promotion.yaml gitlab-generated",
        ".tekton/rpms-on-push.yaml gitlab-generated",
        ".tekton/trigger-manifest-pull-request.json gitlab-generated",
        ".tekton/trigger-manifest-push.json gitlab-generated",
        "konflux-templates/rendered.yml gitlab-generated",
    ]
    for name in sorted(rpa_names):
        lines.append(f"releng/{name}.yaml gitlab-generated")

    return "\n".join(lines) + "\n"


def generate_konflux(
    branch: str, tenant: str, git_repo: str, *,
    private_products: Mapping[str, str] | None = None,
) -> str:
    """Generate Konflux resources."""
    print("Building template variables...", file=sys.stderr)
    variables = build_konflux_variables(branch, tenant, git_repo, private_products=private_products)

    print("Rendering Konflux resources...", file=sys.stderr)
    template_path = ROOT_DIR / "konflux-templates" / "konflux-resources.yml.j2"
    macros_dir = ROOT_DIR / "konflux-templates" / "macros"

    return render_template(template_path, macros_dir, variables)


def _check_its_coverage(its_app_names: set[str], manifest_app_names: set[str]) -> None:
    """Exit non-zero if any application with components lacks an ITS (D2 fail-closed)."""
    uncovered = manifest_app_names - its_app_names
    if uncovered:
        sys.exit(
            f"ERROR: Application(s) {sorted(uncovered)} have components in the "
            f"trigger manifest but no IntegrationTestScenario. Check that application_prefix "
            f"in ci/konflux_rpa_config.yml and private_product in metadata/<package>.json "
            f"produce matching application names."
        )


def main():
    parser = argparse.ArgumentParser(description="Generate Tekton/Konflux resources")
    parser.add_argument("--branch", default="main", help="Git branch (default: main)")
    parser.add_argument("--tenant", default="hummingbird-tenant", help="Konflux tenant")
    parser.add_argument(
        "--git-repo",
        default="https://gitlab.com/redhat/hummingbird/rpms.git",
        help="Git repository URL",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # PAC subcommand
    pac_parser = subparsers.add_parser(
        "pac", help="Generate Pipeline as Code resources"
    )
    pac_parser.add_argument(
        "resource_type",
        choices=["push", "pull-request", "promotion"],
        help="Resource type to generate",
    )

    # Konflux subcommand
    subparsers.add_parser("konflux", help="Generate Konflux resources")

    # Releng subcommand
    subparsers.add_parser("releng", help="Generate releng resources")

    # All subcommand (replaces generate.sh)
    subparsers.add_parser("all", help="Generate all resources (replaces generate.sh)")

    args = parser.parse_args()

    if args.command == "pac":
        print(generate_pac(args.resource_type, args.branch, args.tenant))
    elif args.command == "konflux":
        print(generate_konflux(args.branch, args.tenant, args.git_repo))
    elif args.command == "releng":
        releng_results = generate_releng()
        for name, content in releng_results.items():
            print(f"--- {name} ---", file=sys.stderr)
            print(content)
    elif args.command == "all":
        # Generate all resources (equivalent to generate.sh)
        # Share one validated product snapshot across all outputs in this invocation.
        private_products = MetadataRepository(ROOT_DIR).private_products()
        # Compute ITS manifest entries once; they depend only on branch.
        its_entries = build_its_manifest_entries()

        # Generate PAC resources and trigger manifests together (shared variables)
        # Collect application names from the pull-request manifest to cross-check ITS apps.
        pr_manifest_apps: set[str] | None = None
        for resource_type, event_type in (("push", "push"), ("pull-request", "pull_request")):
            print(f"Building {resource_type} template variables...", file=sys.stderr)
            variables = build_pac_variables(
                args.branch, args.tenant, resource_type, private_products=private_products,
            )

            print(f"Rendering {resource_type} Pipeline as Code resources...", file=sys.stderr)
            template_path = ROOT_DIR / ".tekton" / f"rpms-on-{resource_type}.yaml.j2"
            macros_dir = ROOT_DIR / ".tekton" / "macros"
            pac_result = render_template(template_path, macros_dir, variables).rstrip() + "\n"
            (ROOT_DIR / ".tekton" / f"rpms-on-{resource_type}.yaml").write_text(pac_result)

            # PR manifest carries integration_test_scenarios; push manifest omits the
            # section entirely because rpms has no component-context (post-merge) scenarios.
            its_manifest_entries = its_entries["pr"] if event_type == "pull_request" else its_entries["push"]
            manifest = build_trigger_manifest(variables, event_type, its_manifest_entries)
            manifest_json = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
            (ROOT_DIR / ".tekton" / f"trigger-manifest-{resource_type}.json").write_text(manifest_json)

            if event_type == "pull_request":
                pr_manifest_apps = set(manifest["applications"].keys())

            if resource_type == "push":
                promotion = render_template(
                    ROOT_DIR / ".tekton" / "rpms-on-promotion.yaml.j2", macros_dir, variables
                )
                if not promotion.endswith("\n"):
                    promotion += "\n"
                (ROOT_DIR / ".tekton" / "rpms-on-promotion.yaml").write_text(promotion)

        # Cross-check: every application that has components must also have an ITS.
        # A mismatch means application_prefix in ci/konflux_rpa_config.yml and
        # private_product in metadata/<package>.json produce diverging application
        # names, which would leave some components without integration test coverage.
        its_apps = set(its_entries["pr"].keys())
        if pr_manifest_apps:
            _check_its_coverage(its_apps, pr_manifest_apps)

        konflux = generate_konflux(
            args.branch, args.tenant, args.git_repo, private_products=private_products,
        )
        (ROOT_DIR / "konflux-templates" / "rendered.yml").write_text(konflux)

        releng_results = generate_releng(private_products=private_products)
        releng_dir = ROOT_DIR / "releng"
        releng_dir.mkdir(exist_ok=True)
        for name, content in releng_results.items():
            (releng_dir / f"{name}.yaml").write_text(content)

        print("Generating .gitattributes...", file=sys.stderr)
        (ROOT_DIR / ".gitattributes").write_text(generate_gitattributes())

        print("Generated all resources.", file=sys.stderr)


if __name__ == "__main__":
    main()
