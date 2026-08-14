UI_ORIGINS = ()


class DataUnavailable(RuntimeError):
    pass


def create_app(*a, **k):
    raise NotImplementedError
