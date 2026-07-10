# Korgut Commons

import importlib

_LAZY_SUBMODULES = {"resume_parser", "student_agent", "university_agent"}


def __getattr__(name):
    if name in _LAZY_SUBMODULES:
        module = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
