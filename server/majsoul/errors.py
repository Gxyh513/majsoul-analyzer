"""Mahjong Soul client and authentication exceptions."""
class MajsoulError(Exception):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class MajsoulTokenInvalid(MajsoulError):
    """The server explicitly rejected a token; password fallback is allowed."""


