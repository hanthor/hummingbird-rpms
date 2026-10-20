"""Read ELF program headers without adding inspection tools to the subject."""

import struct

LOADERS = {62: "/lib64/ld-linux-x86-64.so.2", 183: "/lib/ld-linux-aarch64.so.1"}


def _debug_sections(stream, header, endian, wide) -> bool:
    """Recognize separate debuginfo whose allocated contents were stripped to NOBITS."""
    offset = struct.unpack_from(endian + ("Q" if wide else "I"), header, 40 if wide else 32)[0]
    size, count = struct.unpack_from(endian + "HH", header, 58 if wide else 46)
    minimum = 64 if wide else 40
    if not count:
        return False
    if size < minimum:
        raise ValueError("Invalid ELF section header size.")
    stripped = False
    for index in range(count):
        stream.seek(offset + index * size)
        section = stream.read(minimum)
        if len(section) != minimum:
            raise ValueError("Truncated ELF section header.")
        kind = struct.unpack_from(endian + "I", section, 4)[0]
        flags = struct.unpack_from(endian + ("Q" if wide else "I"), section, 8)[0]
        length = struct.unpack_from(endian + ("Q" if wide else "I"), section, 32 if wide else 20)[0]
        if flags & 2 and length:  # SHF_ALLOC
            if kind not in {7, 8}:  # Retained notes and stripped NOBITS sections only.
                return False
            stripped |= kind == 8
    return stripped


def interpreter(stream) -> str | None:
    """Return the dynamic loader for an ELF needing runtime resolution, otherwise None."""
    header = stream.read(64)
    if not header.startswith(b"\x7fELF"):
        return None
    if len(header) != 64 or header[4] not in (1, 2) or header[5] not in (1, 2):
        raise ValueError("Invalid ELF header.")
    endian = "<" if header[5] == 1 else ">"
    wide = header[4] == 2
    machine = struct.unpack_from(endian + "H", header, 18)[0]
    offset = struct.unpack_from(endian + ("Q" if wide else "I"), header, 32 if wide else 28)[0]
    size, count = struct.unpack_from(endian + "HH", header, 54 if wide else 42)
    minimum = 56 if wide else 32
    if count and size < minimum:
        raise ValueError("Invalid ELF program header size.")
    if _debug_sections(stream, header, endian, wide):
        return None
    loader = None
    needed = False
    for index in range(count):
        stream.seek(offset + index * size)
        data = stream.read(minimum)
        if len(data) != minimum:
            raise ValueError("Truncated ELF program header.")
        kind = struct.unpack_from(endian + "I", data)[0]
        file_offset = struct.unpack_from(endian + ("Q" if wide else "I"), data, 8 if wide else 4)[0]
        file_size = struct.unpack_from(endian + ("Q" if wide else "I"), data, 32 if wide else 16)[0]
        if kind == 3:  # PT_INTERP
            stream.seek(file_offset)
            value = stream.read(min(file_size, 4096))
            if not value.endswith(b"\0") or not value.startswith(b"/"):
                raise ValueError("Invalid ELF interpreter.")
            loader = value[:-1].decode("utf-8")
        elif kind == 2:  # PT_DYNAMIC
            stream.seek(file_offset)
            entry_size = 16 if wide else 8
            for _ in range(file_size // entry_size):
                data = stream.read(entry_size)
                if len(data) != entry_size:
                    raise ValueError("Truncated ELF dynamic section.")
                tag = struct.unpack_from(endian + ("q" if wide else "i"), data)[0]
                if tag == 0:  # DT_NULL
                    break
                if tag == 1:  # DT_NEEDED
                    needed = True
    if loader:
        return loader
    if needed:
        if machine not in LOADERS:
            raise ValueError(f"No default dynamic loader for ELF machine {machine}.")
        return LOADERS[machine]
    return None
