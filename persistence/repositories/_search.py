def escape_like(value: str) -> str:
    """Search text is matched literally: LIKE wildcards in it are escaped (use with escape="\\")."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
