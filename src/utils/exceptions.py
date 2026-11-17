class TranscriberServiceError(Exception):
    """Base exception for transcriber service"""

    pass


class DatabaseError(TranscriberServiceError):
    """Database-related errors"""

    pass


class TranscriberError(TranscriberServiceError):
    """Transcriber-related errors"""

    pass


class SummaryError(TranscriberServiceError):
    """Summary generation errors"""

    pass


class ConfigurationError(TranscriberServiceError):
    """Configuration-related errors"""

    pass


class ConnectionError(TranscriberServiceError):
    """Connection-related errors"""

    pass
