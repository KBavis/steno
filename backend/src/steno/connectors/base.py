from pathlib import Path
from typing import Protocol


class GitConnector(Protocol):
    def clone(self, clone_url: str, sha: str | None, dest: Path) -> str:
        """Clone into `dest` and check out `sha` (default branch head if None). Returns the SHA."""
        ...

    def read_file(self, repo: str, path: str, sha: str) -> str:
        """Read one file at a commit from the git host API (`view_file`, `view_flow_code`)."""
        ...

    def list_directory(self, repo: str, path: str, sha: str) -> list[str]: ...
