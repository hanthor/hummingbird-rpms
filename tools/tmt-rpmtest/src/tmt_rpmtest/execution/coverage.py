"""Read component adoption and functional coverage from the canonical fmf tests."""

from pathlib import Path


def functional_coverage(tree: Path, component: str) -> dict:
    """Describe declared scenarios, without claiming that discovery executed them."""
    import fmf

    tests = []
    scenarios = []
    for node in fmf.Tree(str(tree)).prune(keys=["test"]):
        components = node.get("component", [])
        tags = node.get("tag", [])
        if isinstance(components, str):
            components = [components]
        if isinstance(tags, str):
            tags = [tags]
        if component in components and node.get("enabled", True):
            tests.append(node.name)
            if "functional" in tags:
                scenarios.append(node.name)
    return {
        "component": component,
        "result": "declared" if scenarios else "missing",
        "tests": sorted(tests),
        "scenarios": sorted(scenarios),
    }
