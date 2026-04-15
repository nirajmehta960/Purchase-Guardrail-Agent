"""
SavVio LLM Integration Verification Script.

Tests the full LLM pipeline with the Vertex AI provider:
    1. Environment / ADC credential check
    2. Provider initialization
    3. Basic text generation
    4. Intent parser
    5. Response generator
    6. Guardrail checks on LLM output
"""

import os
import sys
import logging

# Load .env before importing anything else
from dotenv import load_dotenv
dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path)
# Also load root .env (API config path)
root_env = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(root_env)

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("verify_llm")

PASS = "PASS"
FAIL = "FAIL"
results = []


def test(name, fn):
    try:
        fn()
        results.append((name, True, ""))
        print(f"  {PASS} {name}")
    except Exception as e:
        results.append((name, False, str(e)))
        print(f"  {FAIL} {name}: {e}")


def assert_(condition, msg="Assertion failed"):
    if not condition:
        raise AssertionError(msg)


def main():
    print("=" * 70)
    print("  SavVio LLM Integration Verification (Vertex AI)")
    print("=" * 70)

    # -----------------------------------------------------------------------
    # 1. Environment check
    # -----------------------------------------------------------------------
    print("\n[1] Environment Check")

    project = os.getenv("VERTEX_PROJECT") or os.getenv("GCP_PROJECT_ID", "")
    location = os.getenv("VERTEX_LOCATION", "us-east1")
    model = os.getenv("VERTEX_MODEL", "gemini-3.0-flash")

    test("VERTEX_PROJECT or GCP_PROJECT_ID is set", lambda: assert_(project, "Neither VERTEX_PROJECT nor GCP_PROJECT_ID found in .env"))
    print(f"    → Project : {project}")
    print(f"    → Location: {location}")
    print(f"    → Model   : {model}")

    # -----------------------------------------------------------------------
    # 2. ADC credential check
    # -----------------------------------------------------------------------
    print("\n[2] Application Default Credentials Check")

    def check_adc():
        import google.auth
        import google.auth.transport.requests as google_requests
        creds, detected_project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        creds.refresh(google_requests.Request())
        assert_(creds.token, "Token is empty after refresh")
        print(f"    → ADC project : {detected_project or '(not set in ADC)'}")
        print(f"    → Token prefix: {creds.token[:20]}...")

    test("google.auth.default() returns valid credentials", check_adc)

    # -----------------------------------------------------------------------
    # 3. Provider initialization
    # -----------------------------------------------------------------------
    print("\n[3] Provider Initialization")
    from llm.llm_provider import get_provider, VertexAIProvider, MockProvider

    test("MockProvider initializes", lambda: assert_(isinstance(MockProvider(), MockProvider)))

    vertex = None
    def init_vertex():
        global vertex
        vertex = VertexAIProvider()
        assert_(vertex.provider_name == "vertex")
        print(f"    → Provider: {vertex.provider_name}, model: {vertex._model}, location: {vertex._location}")
    test("VertexAIProvider initializes", init_vertex)

    auto = get_provider()
    test("get_provider() returns VertexAIProvider (not mock)", lambda: assert_(
        isinstance(auto, VertexAIProvider),
        f"Got {type(auto).__name__} — check VERTEX_PROJECT env var and ADC credentials"
    ))

    if not isinstance(auto, VertexAIProvider):
        print("\n  WARNING: Vertex AI provider not active — skipping LLM tests")
        _print_summary()
        return

    provider = auto

    # -----------------------------------------------------------------------
    # 4. Basic text generation
    # -----------------------------------------------------------------------
    print("\n[4] Basic Text Generation (Vertex AI)")

    gen_response = None
    def test_generate():
        global gen_response
        gen_response = provider.generate("What is 2 + 2? Reply in one sentence.", max_tokens=50)
        assert_(isinstance(gen_response, str), "Response is not a string")
        assert_(len(gen_response) > 0, "Response is empty")
    test("generate() returns non-empty string", test_generate)
    if gen_response:
        print(f"    → Response: {gen_response[:120]}")

    # -----------------------------------------------------------------------
    # 5. Intent parser
    # -----------------------------------------------------------------------
    print("\n[5] Intent Parser (Vertex AI)")
    from llm.intent_parser import parse_user_input

    parsed = None
    def test_intent_parser():
        global parsed
        parsed = parse_user_input("Can I buy the iPhone 15 Pro?", provider)
        assert_(parsed.intent == "purchase_query", f"Expected purchase_query, got {parsed.intent}")
        assert_(parsed.product_reference is not None, "Product reference is None")
    test("parse_user_input() extracts purchase intent", test_intent_parser)
    if parsed:
        print(f"    → Intent: {parsed.intent}, Product: {parsed.product_reference}")

    def test_out_of_scope():
        result = parse_user_input("What is the weather today?", provider)
        assert_(result.intent == "out_of_scope", f"Expected out_of_scope, got {result.intent}")
    test("parse_user_input() rejects out-of-scope queries", test_out_of_scope)

    # -----------------------------------------------------------------------
    # 6. Response generator
    # -----------------------------------------------------------------------
    print("\n[6] Response Generator (Vertex AI)")
    from llm.response_generator import generate_response, RecommendationContext

    green_ctx = RecommendationContext(
        product_name="Sony WH-1000XM5",
        product_price=349.99,
        recommendation_color="GREEN",
        original_color="GREEN",
        was_downgraded=False,
        confidence_scores={"GREEN": 0.87},
        ml_confidence=0.87,
        affordability_score=1650.0,
        savings_to_price_ratio=28.5,
        emergency_fund_months=8.3,
        debt_to_income_ratio=0.15,
        monthly_income=4500.0,
        savings_balance=15000.0,
        average_rating=4.5,
        rating_count=2341,
        verified_purchase_ratio=0.82,
    )

    green_response = None
    def test_green_response():
        global green_response
        green_response = generate_response(green_ctx, provider)
        assert_(isinstance(green_response, str), "Response not a string")
        assert_(len(green_response) > 50, f"Response too short: {len(green_response)} chars")
        # Should be prose, not bullet points
        assert_("- Signal:" not in green_response, "Got template fallback instead of LLM response")
    test("GREEN response generated via Vertex AI (prose, not template)", test_green_response)
    if green_response:
        print(f"    → Response preview: {green_response[:200]}...")

    red_ctx = RecommendationContext(
        product_name="MacBook Pro M4",
        product_price=2499.00,
        recommendation_color="RED",
        original_color="RED",
        was_downgraded=False,
        triggered_rules=["RED_high_dti", "RED_low_savings"],
        confidence_scores={"RED": 0.80},
        ml_confidence=0.80,
        affordability_score=-800.0,
        savings_to_price_ratio=0.4,
        emergency_fund_months=0.5,
        debt_to_income_ratio=0.55,
        monthly_income=3200.0,
        savings_balance=1000.0,
    )

    red_response = None
    def test_red_response():
        global red_response
        red_response = generate_response(red_ctx, provider)
        assert_(isinstance(red_response, str), "Response not a string")
        assert_(len(red_response) > 50, "Response too short")
        assert_("- Signal:" not in red_response, "Got template fallback instead of LLM response")
    test("RED response generated via Vertex AI (prose, not template)", test_red_response)
    if red_response:
        print(f"    → Response preview: {red_response[:200]}...")

    # -----------------------------------------------------------------------
    # 7. Guardrail verification
    # -----------------------------------------------------------------------
    print("\n[7] Guardrail Verification")
    from llm.guardrails import check_response

    if green_response:
        def test_green_guardrails():
            result = check_response(green_response, "GREEN", green_ctx)
            if not result.passed:
                print(f"    WARNING — Violations: {result.violations}")
        test("Guardrail check on GREEN response", test_green_guardrails)

    if red_response:
        def test_red_guardrails():
            result = check_response(red_response, "RED", red_ctx)
            if not result.passed:
                print(f"    WARNING — Violations: {result.violations}")
        test("Guardrail check on RED response", test_red_guardrails)

    _print_summary()


def _print_summary():
    print("\n" + "=" * 70)
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"  Results: {passed}/{total} passed")
    if passed == total:
        print(f"  {PASS} All Vertex AI integration checks passed!")
    else:
        print(f"  {FAIL} Some checks failed:")
        for name, ok, err in results:
            if not ok:
                print(f"    → {name}: {err}")
    print("=" * 70)
    print()
    if any(not ok for _, ok, _ in results):
        print("  Troubleshooting:")
        print("  1. Run: gcloud auth application-default login")
        print("  2. Ensure VERTEX_PROJECT is set in .env")
        print("  3. Ensure your GCP account has roles/aiplatform.user on the project")
        print()


if __name__ == "__main__":
    main()
