class RuleError(Exception):
    """Lỗi kèm gợi ý, để lớp tool đổi sang dạng lỗi chung mà agent tự xử lý được."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class NotFound(RuleError):
    pass


class InvalidInput(RuleError):
    pass


class PolicyViolation(RuleError):
    pass
