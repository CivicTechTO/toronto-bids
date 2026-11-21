"""Shared recursive ZIP metadata for attachment indexing and replacement checks."""
import io
import zipfile


class ZipIndexLimitError(ValueError):
    """A bounded traversal cannot establish a complete count for safe replacement."""


def index_zip(zip_path, *, max_depth=8, max_entries=10000, strict=False) -> list[dict]:
    """Recursively list leaf files in a ZIP archive, descending into nested ZIPs.

    Empty, corrupt, encrypted, or over-depth nested ZIPs degrade to a single leaf.
    Exhausting max_entries truncates remaining entries; strict=True raises
    ZipIndexLimitError on either limit.
    """
    with zipfile.ZipFile(zip_path) as zf:
        return _index_zipfile(zf, prefix="", depth=0, budget=[max_entries],
                              max_depth=max_depth, strict=strict)


def _index_zipfile(zf, prefix, depth, budget, *, max_depth, strict):
    from toronto_bids.sources.ariba_versions import read_provenance

    _, metadata_member = read_provenance(zf)
    out = []
    for zi in zf.infolist():
        if zi.is_dir() or zi.filename == metadata_member:
            continue
        if budget[0] <= 0:
            if strict:
                raise ZipIndexLimitError("ZIP entry limit prevents a complete leaf count")
            continue
        path = prefix + zi.filename
        if zi.filename.lower().endswith(".zip") and depth >= max_depth and strict:
            raise ZipIndexLimitError("ZIP depth limit prevents a complete leaf count")
        if zi.filename.lower().endswith(".zip") and depth < max_depth:
            try:
                # Bounds entry count and depth, not per-entry inflated size.
                with zipfile.ZipFile(io.BytesIO(zf.read(zi))) as nested:
                    children = _index_zipfile(nested, path + "/", depth + 1, budget,
                                              max_depth=max_depth, strict=strict)
                if children:
                    out.extend(children)
                    continue
                # Empty nested zip falls through as a leaf.
            except (zipfile.BadZipFile, RuntimeError, OSError):
                pass  # Corrupt/encrypted nested zip falls through as a leaf.
        out.append({
            "filename": zi.filename,
            "path": path,
            "file_size": zi.file_size,
            "crc32": format(zi.CRC & 0xFFFFFFFF, "08x"),
        })
        budget[0] -= 1
    return out

