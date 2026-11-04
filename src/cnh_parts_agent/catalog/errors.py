"""Expected catalog failures retain their code and retry information."""


class PartsError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        *,
        retryable: bool = False,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
        self.retryable, self.retry_after = retryable, retry_after

    def payload(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "retry_after": self.retry_after,
            }
        }
