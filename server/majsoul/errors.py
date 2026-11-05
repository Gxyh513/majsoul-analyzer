"""Mahjong Soul client and authentication exceptions."""
class MajsoulError(Exception):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class MajsoulTokenInvalid(MajsoulError):
    """The server explicitly rejected a token; password fallback is allowed."""


class MajsoulDetailError(MajsoulError):
    """A record-body failure that does not invalidate the WebSocket session."""


class MajsoulDetailFetchError(MajsoulDetailError):
    """The HTTP record body could not be fetched within its bounded budget."""


class MajsoulDetailContentError(MajsoulDetailError):
    """Fresh record data failed structural validation before persistence."""


