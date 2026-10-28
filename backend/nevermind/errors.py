class AppError(Exception):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


class ModelError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(message, 502)
