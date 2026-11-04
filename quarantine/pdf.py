"""Đọc văn bản từ PDF và tách riêng chữ mà người đọc không nhìn thấy.

Chữ trắng hoặc quá nhỏ không được gửi cho LLM, vì người duyệt không thấy nên nó không được phép ảnh hưởng đến kết quả.
Cách này không bắt được mọi kiểu ẩn chữ, ví dụ chữ cùng màu với nền không trắng.
"""

from dataclasses import dataclass
from pathlib import Path

import pymupdf

from rules.errors import InvalidInput

WHITE = 0xFFFFFF
INVISIBLE_FONT_SIZE = 1
MAX_PAGES = 20
MAX_CHARACTERS = 50_000


@dataclass(frozen=True)
class PdfText:
    visible: str
    hidden: list[str]


def _is_hidden(span: dict) -> bool:
    return span["color"] == WHITE or span["size"] <= INVISIBLE_FONT_SIZE


def read_pdf(path: str | Path) -> PdfText:
    try:
        doc = pymupdf.open(path, filetype="pdf")
    except (pymupdf.FileDataError, pymupdf.EmptyFileError, pymupdf.FileNotFoundError) as cause:
        raise InvalidInput(f"Không mở được file PDF {Path(path).name}") from cause
    with doc:
        if doc.needs_pass:
            raise InvalidInput(f"File PDF {Path(path).name} có mật khẩu", "Hãy nhờ người yêu cầu gửi lại file khác")
        if doc.page_count > MAX_PAGES:
            raise InvalidInput(f"File PDF {Path(path).name} dài quá {MAX_PAGES} trang")
        lines: list[str] = []
        hidden: list[str] = []
        for page in doc:
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    shown = ""
                    for span in line["spans"]:
                        if _is_hidden(span):
                            hidden.append(span["text"])
                        else:
                            shown += span["text"]
                    if shown.strip():
                        lines.append(shown)

    visible = "\n".join(lines)
    if not visible.strip():
        raise InvalidInput(
            f"File PDF {Path(path).name} không có văn bản đọc được",
            "v1 chưa đọc được PDF scan, hãy nhờ người yêu cầu gửi file có văn bản",
        )
    if len(visible) > MAX_CHARACTERS:
        raise InvalidInput(f"File PDF {Path(path).name} dài quá {MAX_CHARACTERS} ký tự")
    return PdfText(visible, [text for text in hidden if text.strip()])
