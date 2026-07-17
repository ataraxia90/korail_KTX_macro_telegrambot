"""Helpers for reporting third-party exceptions safely."""


def safe_exception_text(error: BaseException) -> str:
    """Return useful exception text even when ``error.__str__`` is broken."""
    try:
        text = str(error)
        if text:
            return text
    except Exception:
        pass

    try:
        return repr(error)
    except Exception:
        return f"<{type(error).__name__}>"
