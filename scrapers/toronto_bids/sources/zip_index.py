"""Shared recursive ZIP metadata for attachment indexing and replacement checks."""
import io
import zipfile


class ZipIndexLimitError(ValueError):
    """A bounded traversal cannot establish a complete count for safe replacement."""


def index_zip(zip_path, *, max_depth=8, max_entries=10000, strict=False) -> list[dict]:
    """Recursive central-directory listing of a bundle: one dict per LEAF file.

    Nested zips are descended to any depth (a bundle's real documents often live inside
    "Appendix ….zip"), each leaf carrying the full nested `path`. Sizes and CRC32 come from
    each level's central directory; a nested zip must be read (inflated) to reach its own
    directory, so depth and a per-bundle entry budget bound zip bombs. A nested zip that is
    empty, corrupt, encrypted, or past the depth cap degrades to a single leaf rather than
    being lost. The entry budget (`_MAX_ZIP_ENTRIES`) is a different, harder backstop: once it
    hits zero every remaining entry — including one that would otherwise become a nested-zip
    leaf — is skipped outright, not indexed in any form. That is a deliberate truncation for a
    pathological bundle, not a leaf fallback.

    `strict=True` refuses a depth/entry-limited traversal, so an incomplete count cannot
    authorize replacing a fuller archive. Ordinary indexing retains its bounded behavior.
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
            continue                                   # budget exhausted: hard stop, skip outright
        path = prefix + zi.filename
        if zi.filename.lower().endswith(".zip") and depth >= max_depth and strict:
            raise ZipIndexLimitError("ZIP depth limit prevents a complete leaf count")
        if zi.filename.lower().endswith(".zip") and depth < max_depth:
            try:
                # ponytail: caps bound zip count/depth, not per-entry inflated size; add a size
                # cap if a real bundle ever needs it
                with zipfile.ZipFile(io.BytesIO(zf.read(zi))) as nested:
                    children = _index_zipfile(nested, path + "/", depth + 1, budget,
                                              max_depth=max_depth, strict=strict)
                if children:                       # expandable: contribute its leaves, not it
                    out.extend(children)
                    continue
                # empty zip falls through and is kept as a leaf, so nothing is silently dropped
            except (zipfile.BadZipFile, RuntimeError, OSError):
                pass                               # corrupt/encrypted: keep as an opaque leaf
        out.append({
            "filename": zi.filename,
            "path": path,
            "file_size": zi.file_size,
            "crc32": format(zi.CRC & 0xFFFFFFFF, "08x"),
        })
        budget[0] -= 1
    return out

