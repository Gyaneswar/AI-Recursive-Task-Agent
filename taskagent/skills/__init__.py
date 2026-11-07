from importlib import import_module

from .base import Skill

BUILTIN = {"jobs": "taskagent.skills.jobs:JobsSkill"}


def load_skill(name: str) -> type[Skill]:
    target = BUILTIN.get(name, name)
    module, _, attr = target.partition(":")
    if not attr:
        raise ValueError(f"unknown skill '{name}': use one of {sorted(BUILTIN)} or 'package.module:Class'")
    cls = getattr(import_module(module), attr)
    if not (isinstance(cls, type) and issubclass(cls, Skill)):
        raise TypeError(f"{target} is not a Skill subclass")
    return cls
