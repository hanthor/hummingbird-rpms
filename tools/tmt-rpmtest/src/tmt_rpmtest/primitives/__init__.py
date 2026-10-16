"""Built-in primitives. Custom primitives use the same PrimitiveContext interface."""

from tmt_rpmtest.primitives.files import Files
from tmt_rpmtest.primitives.packages import Content, Link, Packages, RuntimeLink

__all__ = ["Content", "Files", "Link", "Packages", "RuntimeLink"]
