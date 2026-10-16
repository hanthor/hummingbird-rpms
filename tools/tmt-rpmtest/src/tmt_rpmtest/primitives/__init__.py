"""Built-in primitives. Custom primitives use the same PrimitiveContext interface."""

from tmt_rpmtest.primitives.files import Files
from tmt_rpmtest.primitives.packages import Packages

__all__ = ["Files", "Packages"]
