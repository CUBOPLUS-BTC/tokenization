from __future__ import annotations

from pathlib import Path
import re


EXCLUDED_DIRS = {
    ".git",
    ".github",
    ".venv",
    "__pycache__",
    "auth",
    "env",
    "infra",
    "site-packages",
    "specs",
    "tests",
    "venv",
}

TEXT_EXTENSIONS = {
    ".env",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

PEM_PRIVATE_KEY = re.compile(r"BEGIN [A-Z ]*PRIVATE KEY", re.IGNORECASE)
SLACK_TOKEN = re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}", re.IGNORECASE)
ASSIGNMENT = re.compile(
    r"(?i)\b(secret_key|api_key|client_secret|access_token|password)\b\s*[:=]\s*(?P<value>.+)$"
)
TYPE_ANNOTATION_PREFIXES = (
    "any",
    "bool",
    "bytes",
    "datetime",
    "dict",
    "float",
    "int",
    "list",
    "none",
    "object",
    "path",
    "str",
    "tuple",
    "uuid",
)
RUNTIME_REFERENCE_PREFIXES = (
    "$",
    "${",
    "field(",
    "getenv(",
    "none",
    "os.environ",
    "os.getenv(",
    "self.",
    "settings.",
)
PLACEHOLDER_FRAGMENTS = {
    "change-me",
    "changeme",
    "example",
    "placeholder",
    "sample",
}
UNQUOTED_LITERAL = re.compile(r"^[A-Za-z0-9_./+=:-]{12,}$")


def _should_skip(path: Path) -> bool:
    return any(part in EXCLUDED_DIRS for part in path.parts)


def _looks_like_type_annotation(value: str) -> bool:
    normalized = value.lower().lstrip()
    return any(
        normalized == prefix
        or normalized.startswith(f"{prefix} ")
        or normalized.startswith(f"{prefix} |")
        or normalized.startswith(f"{prefix},")
        or normalized.startswith(f"{prefix}]")
        or normalized.startswith(f"{prefix})")
        for prefix in TYPE_ANNOTATION_PREFIXES
    )


def _is_probable_secret_value(raw_value: str) -> bool:
    value = raw_value.strip().rstrip(",")
    if not value:
        return False

    lowered = value.lower()
    if any(lowered.startswith(prefix) for prefix in RUNTIME_REFERENCE_PREFIXES):
        return False
    if _looks_like_type_annotation(value):
        return False

    if value[:1] in {'"', "'"} and value[-1:] == value[:1] and len(value) >= 10:
        literal = value[1:-1].strip()
        literal_lower = literal.lower()
        if len(literal) < 8:
            return False
        if any(fragment in literal_lower for fragment in PLACEHOLDER_FRAGMENTS):
            return False
        return True

    if UNQUOTED_LITERAL.fullmatch(value) and not any(fragment in lowered for fragment in PLACEHOLDER_FRAGMENTS):
        return True

    return False


def _scan_file(path: Path) -> list[tuple[int, str]]:
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []

    findings: list[tuple[int, str]] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        if PEM_PRIVATE_KEY.search(line) or SLACK_TOKEN.search(line):
            findings.append((line_number, line))
            continue

        match = ASSIGNMENT.search(line)
        if match and _is_probable_secret_value(match.group("value")):
            findings.append((line_number, line))

    return findings


def main() -> int:
    findings: list[tuple[Path, int, str]] = []

    for path in Path(".").rglob("*"):
        if not path.is_file() or _should_skip(path):
            continue
        if path.suffix and path.suffix.lower() not in TEXT_EXTENSIONS:
            continue

        for line_number, line in _scan_file(path):
            findings.append((path, line_number, line))

    if findings:
        for path, line_number, line in findings:
            print(f"{path}:{line_number}:{line}")
        print("Secrets detected!")
        return 1

    print("No secrets detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
