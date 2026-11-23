"""Deterministic mock classifier; no network and no hidden tool execution."""
from lending_lab.ai.context import deterministic_plan
from lending_lab.ai.schemas import AssistantPlan


class MockProvider:
    def classify(self, question: str) -> AssistantPlan:
        return deterministic_plan(question)
