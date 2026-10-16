from .tables import ExportFormat


def export_formats(request) -> dict:
    """``export_formats`` for download buttons: one per format a table can be downloaded in."""
    return {"export_formats": ExportFormat}
