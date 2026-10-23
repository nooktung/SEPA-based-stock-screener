"""Allowlisted dashboard pages, shared by the local server and static exporter."""
PAGE_PATHS = (
    "/danh-sach-chinh",
    "/theo-doi-mo-rong",
    "/danh-sach-cua-toi",
    "/dau-nganh",
    "/toan-thi-truong",
    "/kham-pha-nganh",
    "/theo-doi-nganh",
    "/tin-tuc",
    "/hoc",
)


def is_page_path(path: str) -> bool:
    clean = path.rstrip("/") or "/"
    if clean.endswith(".html"):
        clean = clean[:-5]
    return clean in ("/", "/index", *PAGE_PATHS)


def write_page_aliases(directory):
    """Pages serves /name.html at /name; retain a real file for direct visits."""
    source = (directory / "index.html").read_bytes()
    for path in PAGE_PATHS:
        (directory / (path[1:] + ".html")).write_bytes(source)
