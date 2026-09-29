from abc import ABC, abstractmethod


class LLMProvider(ABC):

    @abstractmethod
    def generate_sql(
        self,
        question: str,
        schema: str,
    ) -> str:
        pass

    @abstractmethod
    def explain_result(
        self,
        question: str,
        sql: str,
        result: str,
    ) -> str:
        pass
