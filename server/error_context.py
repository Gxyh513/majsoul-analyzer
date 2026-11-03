"""Credential-safe exception context without messages or arbitrary file paths."""
from __future__ import annotations


def _safe_error_context(error: BaseException) -> str:
    """Report exception type and source line, never its message or locals."""
    locations = []
    seen = set()
    current = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        tb = current.__traceback__
        while tb is not None:
            # Emit only fixed package paths and line numbers, never exception
            # messages, locals, absolute paths, or user-defined filenames.
            filename = tb.tb_frame.f_code.co_filename
            for module in ("server/app.py", "server/account_pool.py", "server/engine/reviewer.py", "server/majsoul/convert.py"):
                if filename.replace("\\", "/").endswith("/" + module):
                    locations.append(f"{module}:{tb.tb_lineno}")
                    break
            tb = tb.tb_next
        current = current.__cause__
    code = getattr(error, "code", None)
    safe_code = f" code={code}" if isinstance(code, int) and not isinstance(code, bool) else ""
    return f"{type(error).__name__}{safe_code} at {','.join(locations[-4:]) or 'external'}"


