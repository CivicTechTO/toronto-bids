from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class Exporter(Protocol):
    """The publish seam: turn the store into a published artifact at out_path.

    Implementations differ by destination/format, not by document shape — they
    all serialize build_export_document(conn). Future: Parquet, static site, API.
    """

    name: str

    def export(self, conn, out_path, generated_at: str | None = None) -> Path:
        ...
