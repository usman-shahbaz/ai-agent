import boto3

from app.core.config import settings
from app.llm.provider import LLMProvider
from app.agents.prompts import SQL_PROMPT, EXPLANATION_PROMPT


class BedrockProvider(LLMProvider):

    def __init__(self):
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=settings.aws_region,
        )

        self.model_id = settings.bedrock_model_id

    def _invoke(self, prompt: str) -> str:

        response = self.client.converse(
            modelId=self.model_id,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": prompt
                        }
                    ],
                }
            ],
            inferenceConfig={
                "temperature": 0.0,
                "maxTokens": 2000,
            },
        )

        return response["output"]["message"]["content"][0]["text"]

    def generate_sql(
        self,
        question: str,
        schema: str,
    ) -> str:

        prompt = SQL_PROMPT.format(
            question=question,
            schema=schema,
        )

        return self._invoke(prompt)

    def explain_result(
        self,
        question: str,
        sql: str,
        result: str,
    ) -> str:

        prompt = EXPLANATION_PROMPT.format(
            question=question,
            sql=sql,
            result=result,
        )

        return self._invoke(prompt)
