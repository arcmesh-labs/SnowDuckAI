from pathlib import Path

from mcp.server.fastmcp import FastMCP

BASE_DIR = Path("/home/alex/project-red-v2")

mcp = FastMCP("project-red-v2")


def _check_path(path: str) -> tuple[Path, str | None]:
    if path in ("", "/"):
        return BASE_DIR.resolve(), None
    target = (BASE_DIR / path).resolve()
    if not str(target).startswith(str(BASE_DIR.resolve())):
        return target, "Error: path is outside BASE_DIR"
    return target, None


@mcp.tool()
def read_file(path: str) -> str:
    """Read and return the contents of a file."""
    target, err = _check_path(path)
    if err:
        return err
    if not target.is_file():
        return f"Error: not a file: {path}"
    return target.read_text()


@mcp.tool()
def list_directory(path: str) -> str:
    """List files and directories in a directory."""
    target, err = _check_path(path)
    if err:
        return err
    if not target.is_dir():
        return f"Error: not a directory: {path}"
    return "\n".join(entry.name for entry in sorted(target.iterdir()))


@mcp.tool()
def search_content(path: str, query: str) -> list[str]:
    """Recursively search for files containing query, returning matching paths and lines."""
    target, err = _check_path(path)
    if err:
        return [err]
    if not target.is_dir():
        return [f"Error: not a directory: {path}"]
    results = []
    for file in target.rglob("*"):
        if file.is_file():
            try:
                for lineno, line in enumerate(file.read_text().splitlines(), 1):
                    if query in line:
                        results.append(f"{file}:{lineno}: {line}")
            except (OSError, UnicodeDecodeError):
                pass
    return results


if __name__ == "__main__":
    mcp.run()
