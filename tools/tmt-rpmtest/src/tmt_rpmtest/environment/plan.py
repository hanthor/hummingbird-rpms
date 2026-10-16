"""Read the RPM environment from a canonical fmf plan."""

from pathlib import Path


def settings_from_plan(path: Path) -> tuple[dict, Path]:
    import fmf

    plan = path.resolve()
    tree = fmf.Tree(str(plan.parent))
    root = Path(tree.root)
    name = "/" + plan.relative_to(root).with_suffix("").as_posix()
    node = tree.find(name)
    settings = node.get("prepare") if node else None
    if not isinstance(settings, dict) or settings.get("how") != "rpmtest":
        raise ValueError("The selected plan must contain one RPM prepare phase.")
    return settings.copy(), root
