"""Optional official Google Gen AI SDK adapter. It receives question text only."""
from lending_lab.ai.prompts import SYSTEM_INSTRUCTION
from lending_lab.ai.schemas import AssistantPlan


class GeminiProvider:
    def __init__(self, api_key: str, model: str, timeout_seconds: int = 5):
        if not api_key or not model:
            raise ValueError("Gemini key and model must be configured")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    def classify(self, question: str) -> AssistantPlan:
        # No functions/tools are supplied to the SDK, so automatic function calling
        # is impossible. Pydantic response schema constrains output to the allowlist.
        from google import genai
        from google.genai import types

        with genai.Client(api_key=self.api_key,
                          http_options=types.HttpOptions(timeout=self.timeout_seconds * 1000,
                                                         retry_options=types.HttpRetryOptions(attempts=1))) as client:
            response = client.models.generate_content(
                model=self.model,
                contents=f"Untrusted borrower question:\n{question}",
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json", response_schema=AssistantPlan,
                    max_output_tokens=128, temperature=0,
                ),
            )
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, AssistantPlan):
            return parsed
        if parsed is not None:
            return AssistantPlan.model_validate(parsed)
        text = getattr(response, "text", None)
        if not text:
            raise ValueError("provider returned no structured response")
        return AssistantPlan.model_validate_json(text)
