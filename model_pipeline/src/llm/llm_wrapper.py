import re
import time
import os
import requests


GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = "llama-3.1-8b-instant"


def call_groq(prompt: str) -> str:
    """
    Calls Groq API with retry logic.
    Uses llama-3.1-8b-instant (free tier, very fast).
    """
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 200,
        "temperature": 0.3,
    }

    max_retries = 3
    delay = 2

    for attempt in range(max_retries):
        try:
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=30,
            )
            if response.status_code == 429:
                print(f"[LLM] Rate limited. Retrying in {delay} sec...")
                time.sleep(delay)
                delay *= 2
                continue
            response.raise_for_status()
            data = response.json()
            if "choices" not in data:
                print(f"[LLM] Unexpected response: {data}")
                raise KeyError("choices")
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"[LLM ERROR] Attempt {attempt + 1}: {e}")
            time.sleep(delay)
            delay *= 2

    return "Unable to generate explanation safely."


class LLMWrapper:
    def __init__(self, llm_client=None):
        # Use Groq LLM by default
        self.llm = llm_client if llm_client is not None else call_groq

    def generate_recommendation(
        self,
        decision,
        financial_features,
        product_features,
        ml_confidence
    ):
        start_time = time.time()

        prompt = self._build_prompt(
            decision,
            financial_features,
            product_features,
            ml_confidence
        )

        response = self._call_llm(prompt)

        safe_response, guardrail_triggered = self._apply_guardrails(
            response,
            decision,
            financial_features,
            product_features
        )

        latency = time.time() - start_time

        return {
            "decision": decision,
            "explanation": safe_response,
            "latency": latency,
            "explanation_length": len(safe_response),
            "guardrail_triggered": guardrail_triggered
        }

    def _build_prompt(self, decision, financial_features, product_features, ml_confidence):
        return f"""
You are a financial assistant helping a user decide whether to make a purchase.

STRICT RULES:
- You MUST follow the given decision exactly
- You CANNOT change the decision
- You MUST NOT generate new numbers
- You can ONLY use the numbers provided
- You MUST NOT give advice about investing, loans, or unrelated topics

FINAL DECISION: {decision}

FINANCIAL SIGNALS:
- affordability_score: {financial_features.get("affordability_score")}
- price_to_income_ratio: {financial_features.get("price_to_income_ratio")}
- savings_to_price_ratio: {financial_features.get("savings_to_price_ratio")}

PRODUCT CONTEXT:
- price: {product_features.get("price")}
- category: {product_features.get("category")}

MODEL CONFIDENCE: {ml_confidence}

TASK:
Explain WHY this decision was made.

FORMAT:
- 2 to 3 short sentences
- Simple and clear language
- Focus on affordability and risk

IMPORTANT:
- If decision is RED -> explain why it is risky
- If decision is YELLOW -> explain caution
- If decision is GREEN -> explain why it is safe
"""

    def _call_llm(self, prompt):
        try:
            return self.llm(prompt)
        except Exception as e:
            print(f"[LLMWrapper] LLM call failed: {e}")
            return "Could not generate explanation."

    def _apply_guardrails(self, response, decision, financial_features, product_features):
        # Default flag
        guardrail_triggered = False

        # Rule 0: Empty or invalid response
        if not response or len(response.strip()) == 0:
            return "Unable to generate explanation safely.", True

        response_lower = response.lower()

        # Rule 1: Decision consistency
        if decision == "RED" and "safe" in response_lower:
            return "This purchase is not recommended due to financial risk.", True

        if decision == "GREEN" and "not recommended" in response_lower:
            return "This purchase is considered safe based on your financial condition.", True

        if decision == "YELLOW" and "safe" in response_lower:
            return "This purchase should be approached with caution based on your financial situation.", True

        # Rule 2: Prevent hallucinated numbers (regex based)
        numbers_in_response = re.findall(r'\d+\.?\d*', response)

        allowed_numbers = [
            str(financial_features.get("affordability_score")),
            str(financial_features.get("price_to_income_ratio")),
            str(financial_features.get("savings_to_price_ratio")),
            str(product_features.get("price"))
        ]

        for num in numbers_in_response:
            if num not in allowed_numbers:
                return "Explanation restricted due to inconsistent financial values.", True

        # Rule 3: Block out-of-scope advice
        blocked_keywords = ["invest", "stock", "crypto", "loan", "medical"]

        for keyword in blocked_keywords:
            if keyword in response_lower:
                return "This system only provides purchase-related guidance.", True

        return response, guardrail_triggered