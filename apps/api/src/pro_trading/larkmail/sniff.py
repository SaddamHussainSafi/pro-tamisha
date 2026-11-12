"""File type from the bytes (not the name the sender chose)."""

_MAGIC = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"PK\x03\x04", "application/zip"),
    (b"MZ", "application/x-msdownload"),
    (b"\x7fELF", "application/x-executable"),
    (b"\xcf\xfa\xed\xfe", "application/x-mach-binary"),
    (b"\xd0\xcf\x11\xe0", "application/x-ole-storage"),
)

# Shown in the browser (inline preview); everything else is download-only.
PREVIEWABLE = {"application/pdf", "image/png", "image/jpeg", "image/gif"}


def detect(data: bytes, declared: str) -> str:
    head = data[:16]
    for magic, mime in _MAGIC:
        if head.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if declared.startswith("text/"):
        return "text/plain"
    return "application/octet-stream"
