class ProviderError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ProviderParseError(ProviderError):
    pass
