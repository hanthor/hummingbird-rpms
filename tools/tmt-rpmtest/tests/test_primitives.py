"""Exercise extensions through the same context as built-in primitives."""

import io
import shutil
import tarfile
from types import SimpleNamespace

import pytest

from tmt_rpmtest import PrimitiveContext
from tmt_rpmtest.subject import Subject


def test_custom_primitive_shares_evidence_and_subject_lifetime(tmp_path):
    original = tmp_path / "root.tar"
    with tarfile.open(original, "w") as archive:
        member = tarfile.TarInfo("message")
        member.size = 5
        archive.addfile(member, io.BytesIO(b"hello"))
    session = SimpleNamespace(snapshot=lambda path: shutil.copyfile(original, path))

    class Contents:
        def __init__(self, context):
            self.context = context

        def read(self, path):
            with self.context.snapshot("contents") as snapshot:
                with tarfile.open(snapshot) as archive:
                    return archive.extractfile(path).read()

    with PrimitiveContext(session, tmp_path, "unused") as context:
        target = Subject(context)
        contents = target.use(Contents)
        assert contents.read("message") == b"hello"
        assert target.files.stat("message").size == 5
        operations = [path for path in tmp_path.iterdir() if path.is_dir()]
        assert len(operations) == 2
        assert not list(tmp_path.glob("*/snapshot.tar"))
    with pytest.raises(RuntimeError, match="closed"):
        contents.read("message")


@pytest.mark.parametrize("failure", ["export", "reader"])
def test_snapshot_failures_remove_partial_archives_and_keep_error_evidence(tmp_path, failure):
    def export(path):
        path.write_bytes(b"partial snapshot")
        if failure == "export":
            raise OSError("export failed")

    session = SimpleNamespace(snapshot=export)
    with PrimitiveContext(session, tmp_path, "unused") as context:
        with pytest.raises(OSError, match="failed"):
            with context.snapshot("broken"):
                raise OSError("reader failed")
    assert not list(tmp_path.glob("*/snapshot.tar"))
    assert next(tmp_path.glob("*/error.txt")).read_text() == f"{failure} failed\n"
