__all__ = ["app"]


def __getattr__(name):
    if name == "app":
        from .app import app

        globals()[name] = app
        return app
    raise AttributeError(name)
