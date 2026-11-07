from abc import ABC, abstractmethod


class Skill(ABC):
    name = "base"
    description = ""

    def __init__(self, ctx):
        self.ctx = ctx

    @abstractmethod
    def run(self, params: dict) -> str:
        ...
