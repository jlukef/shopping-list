from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.shopping_list.config import build_receipt_ai_settings
from src.shopping_list.receipt_extraction import (
    AllExtractionAttemptsFailed,
    AnthropicExtractor,
    AttemptRecord,
    ExtractionError,
    ExtractionInvalid,
    ExtractionRefused,
    EXTRACTION_PROMPT,
    ExtractorRegistry,
    GeminiExtractor,
    OpenAIExtractor,
    ReceiptExtractor,
    extract_with_fallback,
    validate_extraction_payload,
)

GOOD_PAYLOAD = {
    "shop_name_guess": "Morrisons",
    "purchase_date": "2026-06-30",
    "currency": "GBP",
    "subtotal_pennies": 145,
    "total_pennies": 145,
    "lines": [{
        "raw_text": "MILK 1.45", "name": "Milk", "quantity": 1, "unit": "L",
        "unit_price_pennies": 145, "line_total_pennies": 145,
        "category": "item", "confidence": 0.95,
    }],
}


class ConfigValidationTests(unittest.TestCase):
    def test_unconfigured_by_default(self) -> None:
        settings = build_receipt_ai_settings(
            anthropic_api_key="", gemini_api_key="", openai_api_key="",
            options_raw="", default_raw="auto", fallbacks_raw="",
        )
        self.assertFalse(settings.configured)
        self.assertEqual(settings.enabled_options(), ())

    def test_valid_config_parses(self) -> None:
        settings = build_receipt_ai_settings(
            anthropic_api_key="sk-ant-x", gemini_api_key="", openai_api_key="",
            options_raw="claude-fast=anthropic:claude-haiku-4-5;gemini-fast=google:gemini-3.5-flash",
            default_raw="auto",
            fallbacks_raw="claude-fast;gemini-fast",
        )
        self.assertTrue(settings.configured)
        self.assertEqual([o.alias for o in settings.enabled_options()], ["claude-fast"])
        self.assertTrue(settings.is_enabled("claude-fast"))
        self.assertFalse(settings.is_enabled("gemini-fast"))  # no gemini key configured

    def test_duplicate_alias_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_receipt_ai_settings(
                anthropic_api_key="x", gemini_api_key="", openai_api_key="",
                options_raw="a=anthropic:m1;a=anthropic:m2",
                default_raw="auto", fallbacks_raw="a",
            )

    def test_unknown_provider_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_receipt_ai_settings(
                anthropic_api_key="x", gemini_api_key="", openai_api_key="",
                options_raw="a=microsoft:model",
                default_raw="auto", fallbacks_raw="a",
            )

    def test_empty_fallback_list_rejected_when_options_configured(self) -> None:
        with self.assertRaises(ValueError):
            build_receipt_ai_settings(
                anthropic_api_key="x", gemini_api_key="", openai_api_key="",
                options_raw="a=anthropic:m", default_raw="auto", fallbacks_raw="",
            )

    def test_fallback_referencing_unknown_alias_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_receipt_ai_settings(
                anthropic_api_key="x", gemini_api_key="", openai_api_key="",
                options_raw="a=anthropic:m", default_raw="auto", fallbacks_raw="b",
            )

    def test_default_must_be_auto_or_an_enabled_alias(self) -> None:
        with self.assertRaises(ValueError):
            build_receipt_ai_settings(
                anthropic_api_key="", gemini_api_key="", openai_api_key="x",
                options_raw="a=anthropic:m;b=openai:m2",
                default_raw="a",  # anthropic key not set, so 'a' isn't enabled
                fallbacks_raw="b",
            )

    def test_no_provider_key_configured_at_all_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_receipt_ai_settings(
                anthropic_api_key="", gemini_api_key="", openai_api_key="",
                options_raw="a=anthropic:m", default_raw="auto", fallbacks_raw="a",
            )


class ValidationPayloadTests(unittest.TestCase):
    def test_prompt_requests_purchase_record_not_full_transcription(self) -> None:
        self.assertIn("structured PURCHASE RECORD", EXTRACTION_PROMPT)
        self.assertIn("postal addresses", EXTRACTION_PROMPT)
        self.assertIn("payment method", EXTRACTION_PROMPT)
        self.assertIn("Consolidate consecutive identical products", EXTRACTION_PROMPT)
        self.assertIn("unit_price_pennies", EXTRACTION_PROMPT)
        self.assertIn("UK numeric dates are DAY/MONTH/YEAR", EXTRACTION_PROMPT)

    def test_valid_payload_round_trips(self) -> None:
        result = validate_extraction_payload(
            GOOD_PAYLOAD, provider="anthropic", model="claude-haiku-4-5", raw_json=json.dumps(GOOD_PAYLOAD),
        )
        self.assertEqual(result.currency, "GBP")
        self.assertEqual(len(result.lines), 1)
        self.assertEqual(result.lines[0].category, "item")

    def test_unsupported_currency_rejected(self) -> None:
        bad = {**GOOD_PAYLOAD, "currency": "JPY"}
        with self.assertRaises(ExtractionInvalid):
            validate_extraction_payload(bad, provider="p", model="m", raw_json="{}")

    def test_empty_lines_rejected(self) -> None:
        bad = {**GOOD_PAYLOAD, "lines": []}
        with self.assertRaises(ExtractionInvalid):
            validate_extraction_payload(bad, provider="p", model="m", raw_json="{}")

    def test_too_many_lines_rejected(self) -> None:
        bad = {**GOOD_PAYLOAD, "lines": [GOOD_PAYLOAD["lines"][0]] * 201}
        with self.assertRaises(ExtractionInvalid):
            validate_extraction_payload(bad, provider="p", model="m", raw_json="{}")

    def test_unknown_category_rejected(self) -> None:
        line = {**GOOD_PAYLOAD["lines"][0], "category": "bogus"}
        bad = {**GOOD_PAYLOAD, "lines": [line]}
        with self.assertRaises(ExtractionInvalid):
            validate_extraction_payload(bad, provider="p", model="m", raw_json="{}")

    def test_confidence_out_of_range_rejected(self) -> None:
        line = {**GOOD_PAYLOAD["lines"][0], "confidence": 1.5}
        bad = {**GOOD_PAYLOAD, "lines": [line]}
        with self.assertRaises(ExtractionInvalid):
            validate_extraction_payload(bad, provider="p", model="m", raw_json="{}")

    def test_impossible_total_rejected(self) -> None:
        bad = {**GOOD_PAYLOAD, "total_pennies": 100_000_00}  # wildly inconsistent with the one 145p line
        with self.assertRaises(ExtractionInvalid):
            validate_extraction_payload(bad, provider="p", model="m", raw_json="{}")

    def test_null_name_and_quantity_are_accepted(self) -> None:
        line = {**GOOD_PAYLOAD["lines"][0], "name": None, "quantity": None, "unit": None}
        payload = {**GOOD_PAYLOAD, "lines": [line]}
        result = validate_extraction_payload(payload, provider="p", model="m", raw_json="{}")
        self.assertIsNone(result.lines[0].name)
        self.assertIsNone(result.lines[0].quantity)

    def test_administrative_other_lines_are_omitted(self) -> None:
        address = {
            "raw_text": "Hilmore House, Gain Lane, Bradford", "name": None,
            "quantity": None, "unit": None, "unit_price_pennies": None,
            "line_total_pennies": None, "category": "other", "confidence": 0.99,
        }
        payment = {**address, "raw_text": "VISA CONTACTLESS **** 1234"}
        payload = {**GOOD_PAYLOAD, "lines": [address, GOOD_PAYLOAD["lines"][0], payment]}
        result = validate_extraction_payload(payload, provider="p", model="m", raw_json="{}")
        self.assertEqual([line.name for line in result.lines], ["Milk"])

    def test_consecutive_identical_products_are_consolidated(self) -> None:
        cucumber = {
            "raw_text": "CUCUMBER 0.80", "name": "Cucumber", "quantity": 1,
            "unit": "each", "unit_price_pennies": 80, "line_total_pennies": 80,
            "category": "item", "confidence": 0.94,
        }
        payload = {**GOOD_PAYLOAD, "total_pennies": 240, "subtotal_pennies": 240,
                   "lines": [cucumber, cucumber, cucumber]}
        result = validate_extraction_payload(payload, provider="p", model="m", raw_json="{}")
        self.assertEqual(len(result.lines), 1)
        self.assertEqual(result.lines[0].name, "Cucumber")
        self.assertEqual(result.lines[0].quantity, 3)
        self.assertEqual(result.lines[0].unit_price_pennies, 80)
        self.assertEqual(result.lines[0].line_total_pennies, 240)

    def test_repeated_products_without_explicit_unit_price_can_infer_it(self) -> None:
        cucumber = {
            "raw_text": "CUCUMBER 0.80", "name": "Cucumber", "quantity": 1,
            "unit": None, "unit_price_pennies": None, "line_total_pennies": 80,
            "category": "item", "confidence": 0.9,
        }
        payload = {**GOOD_PAYLOAD, "total_pennies": 160, "subtotal_pennies": 160,
                   "lines": [cucumber, cucumber]}
        result = validate_extraction_payload(payload, provider="p", model="m", raw_json="{}")
        self.assertEqual(result.lines[0].quantity, 2)
        self.assertEqual(result.lines[0].unit_price_pennies, 80)
        self.assertEqual(result.lines[0].line_total_pennies, 160)

    def test_same_product_at_different_prices_is_not_consolidated(self) -> None:
        first = {**GOOD_PAYLOAD["lines"][0], "name": "Cucumber", "raw_text": "CUCUMBER 0.80",
                 "unit": "each", "unit_price_pennies": 80, "line_total_pennies": 80}
        second = {**first, "raw_text": "CUCUMBER 0.90", "unit_price_pennies": 90,
                  "line_total_pennies": 90}
        payload = {**GOOD_PAYLOAD, "total_pennies": 170, "subtotal_pennies": 170,
                   "lines": [first, second]}
        result = validate_extraction_payload(payload, provider="p", model="m", raw_json="{}")
        self.assertEqual(len(result.lines), 2)

    def test_weighted_products_are_not_consolidated(self) -> None:
        weighted = {**GOOD_PAYLOAD["lines"][0], "name": "Bananas", "raw_text": "BANANAS 0.456kg",
                    "quantity": 0.456, "unit": "kg", "unit_price_pennies": 120,
                    "line_total_pennies": 55}
        payload = {**GOOD_PAYLOAD, "total_pennies": 110, "subtotal_pennies": 110,
                   "lines": [weighted, weighted]}
        result = validate_extraction_payload(payload, provider="p", model="m", raw_json="{}")
        self.assertEqual(len(result.lines), 2)

    def test_already_consolidated_multiplier_remains_one_row(self) -> None:
        cucumber_x3 = {
            "raw_text": "CUCUMBER x3 @ 0.80 2.40", "name": "Cucumber", "quantity": 3,
            "unit": "each", "unit_price_pennies": 80, "line_total_pennies": 240,
            "category": "item", "confidence": 0.96,
        }
        payload = {**GOOD_PAYLOAD, "total_pennies": 240, "subtotal_pennies": 240,
                   "lines": [cucumber_x3]}
        result = validate_extraction_payload(payload, provider="p", model="m", raw_json="{}")
        self.assertEqual(len(result.lines), 1)
        self.assertEqual(result.lines[0].quantity, 3)

    def test_non_dict_payload_rejected(self) -> None:
        with self.assertRaises(ExtractionInvalid):
            validate_extraction_payload("not a dict", provider="p", model="m", raw_json="{}")


class _FakeExtractor:
    """A minimal ReceiptExtractor for orchestration tests — no network, no SDKs."""

    def __init__(self, provider: str, model: str, *, outcome: str = "success"):
        self.provider = provider
        self.model = model
        self._outcome = outcome
        self.calls = 0

    async def extract(self, image_bytes: bytes, mime_type: str):
        self.calls += 1
        if self._outcome == "success":
            return validate_extraction_payload(
                GOOD_PAYLOAD, provider=self.provider, model=self.model, raw_json=json.dumps(GOOD_PAYLOAD),
            )
        error_types = {
            "refused": ExtractionRefused, "invalid": ExtractionInvalid, "error": ExtractionError,
        }
        raise error_types[self._outcome]("simulated failure")


def _registry_with(fakes: dict[str, _FakeExtractor], fallback_order: tuple[str, ...]) -> ExtractorRegistry:
    registry = ExtractorRegistry.__new__(ExtractorRegistry)
    registry._settings = MagicMock(fallback_order=fallback_order)
    registry._extractors = fakes
    return registry


class FallbackOrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_mode_stops_at_first_success(self) -> None:
        first = _FakeExtractor("anthropic", "m1", outcome="refused")
        second = _FakeExtractor("google", "m2", outcome="success")
        registry = _registry_with({"a": first, "b": second}, ("a", "b"))

        result, attempts = await extract_with_fallback(registry, requested="auto", image_bytes=b"x", mime_type="image/jpeg")

        self.assertEqual(result.provider, "google")
        self.assertEqual([a.outcome for a in attempts], ["refused", "success"])
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 1)

    async def test_all_attempts_failing_raises_with_full_attempt_list(self) -> None:
        first = _FakeExtractor("anthropic", "m1", outcome="invalid")
        second = _FakeExtractor("google", "m2", outcome="error")
        registry = _registry_with({"a": first, "b": second}, ("a", "b"))

        with self.assertRaises(AllExtractionAttemptsFailed) as ctx:
            await extract_with_fallback(registry, requested="auto", image_bytes=b"x", mime_type="image/jpeg")

        self.assertEqual([a.outcome for a in ctx.exception.attempts], ["invalid", "error"])

    async def test_specific_alias_does_not_fall_back_on_failure(self) -> None:
        first = _FakeExtractor("anthropic", "m1", outcome="refused")
        second = _FakeExtractor("google", "m2", outcome="success")
        registry = _registry_with({"a": first, "b": second}, ("a", "b"))

        with self.assertRaises(AllExtractionAttemptsFailed) as ctx:
            await extract_with_fallback(registry, requested="a", image_bytes=b"x", mime_type="image/jpeg")

        self.assertEqual(len(ctx.exception.attempts), 1)
        self.assertEqual(second.calls, 0)  # never tried — a specific pick is one shot only

    async def test_unknown_alias_raises_value_error(self) -> None:
        registry = _registry_with({}, ())
        with self.assertRaises(ValueError):
            await extract_with_fallback(registry, requested="nope", image_bytes=b"x", mime_type="image/jpeg")


class AdapterWiringTests(unittest.IsolatedAsyncioTestCase):
    """Confirms each adapter calls its SDK with the expected shape and parses a
    realistic mocked response — without ever making a real network call."""

    async def test_anthropic_extractor_happy_path(self) -> None:
        import anthropic

        text_block = MagicMock(type="text", text=json.dumps(GOOD_PAYLOAD))
        response = MagicMock(stop_reason="end_turn", content=[text_block])
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=response)

        with patch.object(anthropic, "AsyncAnthropic", return_value=mock_client) as ctor:
            extractor = AnthropicExtractor(model="claude-haiku-4-5", api_key="sk-ant-test")
            result = await extractor.extract(b"fake-jpeg-bytes", "image/jpeg")

        self.assertEqual(result.provider, "anthropic")
        ctor.assert_called_once()
        _, kwargs = mock_client.messages.create.call_args
        self.assertEqual(kwargs["model"], "claude-haiku-4-5")
        self.assertIn("output_config", kwargs)

    async def test_anthropic_extractor_refusal_raises(self) -> None:
        import anthropic

        response = MagicMock(stop_reason="refusal", content=[])
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=response)

        with patch.object(anthropic, "AsyncAnthropic", return_value=mock_client):
            extractor = AnthropicExtractor(model="claude-haiku-4-5", api_key="sk-ant-test")
            with self.assertRaises(ExtractionRefused):
                await extractor.extract(b"fake-jpeg-bytes", "image/jpeg")

    async def test_gemini_extractor_happy_path(self) -> None:
        from google import genai

        interaction = MagicMock(output_text=json.dumps(GOOD_PAYLOAD))
        mock_client = MagicMock()
        mock_client.aio.interactions.create = AsyncMock(return_value=interaction)

        with patch.object(genai, "Client", return_value=mock_client) as ctor:
            extractor = GeminiExtractor(model="gemini-3.5-flash", api_key="test-key")
            result = await extractor.extract(b"fake-jpeg-bytes", "image/jpeg")

        self.assertEqual(result.provider, "google")
        ctor.assert_called_once()
        _, kwargs = mock_client.aio.interactions.create.call_args
        self.assertEqual(kwargs["model"], "gemini-3.5-flash")

    async def test_openai_extractor_happy_path(self) -> None:
        import openai

        message = MagicMock(content=json.dumps(GOOD_PAYLOAD), refusal=None)
        choice = MagicMock(message=message, finish_reason="stop")
        response = MagicMock(choices=[choice])
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=response)

        with patch.object(openai, "AsyncOpenAI", return_value=mock_client) as ctor:
            extractor = OpenAIExtractor(model="gpt-5.4-mini", api_key="sk-test")
            result = await extractor.extract(b"fake-jpeg-bytes", "image/jpeg")

        self.assertEqual(result.provider, "openai")
        ctor.assert_called_once()
        _, kwargs = mock_client.chat.completions.create.call_args
        self.assertEqual(kwargs["model"], "gpt-5.4-mini")
        self.assertEqual(kwargs["response_format"]["json_schema"]["strict"], True)

    async def test_openai_extractor_refusal_raises(self) -> None:
        import openai

        message = MagicMock(content=None, refusal="cannot help with that")
        choice = MagicMock(message=message, finish_reason="stop")
        response = MagicMock(choices=[choice])
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=response)

        with patch.object(openai, "AsyncOpenAI", return_value=mock_client):
            extractor = OpenAIExtractor(model="gpt-5.4-mini", api_key="sk-test")
            with self.assertRaises(ExtractionRefused):
                await extractor.extract(b"fake-jpeg-bytes", "image/jpeg")


if __name__ == "__main__":
    unittest.main()
