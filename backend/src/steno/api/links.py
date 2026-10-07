"""Links to the exact line of a file at the ingested commit (GitHub and GitLab URL shapes)."""


def source_url(
    clone_url: str | None, commit: str | None, path: str | None, line: int | None
) -> str | None:
    if not (clone_url and commit and path) or not clone_url.startswith("https://"):
        return None
    base = clone_url.removesuffix(".git")
    anchor = f"#L{line}" if line else ""
    if "gitlab" in base:
        return f"{base}/-/blob/{commit}/{path}{anchor}"
    return f"{base}/blob/{commit}/{path}{anchor}"
