import difflib
from typing import Literal

from pydantic import BaseModel


class DiffLine(BaseModel):
    type: Literal["equal", "added", "removed"]
    text: str


def compute_diff(base_text: str, tailored_text: str) -> list[DiffLine]:
    """Line-level diff between the base resume and a tailored version, for
    the UI's "see exactly what changed before it's used" diff view."""
    base_lines = base_text.splitlines()
    tailored_lines = tailored_text.splitlines()

    diff_lines: list[DiffLine] = []
    matcher = difflib.SequenceMatcher(a=base_lines, b=tailored_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            diff_lines.extend(DiffLine(type="equal", text=line) for line in base_lines[i1:i2])
        elif tag == "delete":
            diff_lines.extend(DiffLine(type="removed", text=line) for line in base_lines[i1:i2])
        elif tag == "insert":
            diff_lines.extend(DiffLine(type="added", text=line) for line in tailored_lines[j1:j2])
        elif tag == "replace":
            diff_lines.extend(DiffLine(type="removed", text=line) for line in base_lines[i1:i2])
            diff_lines.extend(DiffLine(type="added", text=line) for line in tailored_lines[j1:j2])
    return diff_lines


def summarize_diff(diff_lines: list[DiffLine]) -> str:
    added = sum(1 for line in diff_lines if line.type == "added")
    removed = sum(1 for line in diff_lines if line.type == "removed")
    if added == 0 and removed == 0:
        return "No changes"
    return f"{added} line(s) added, {removed} line(s) removed"
