"""Offline unit tests for Zero-Overage Google Free-Stipend Ledger & Hybrid Helpers."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

SRC = str(Path(__file__).resolve().parents[1] / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from legends_dataforseo import (
    Credentials,
    STIPEND_SKUS,
    business_lookup_hybrid,
    can_use_stipend,
    check_and_reserve,
    cli,
    client,
    google_custom_search,
    google_ledger_path,
    google_pagespeed_insights,
    google_places_lookup,
    pagespeed_hybrid,
    redact_google_key,
    reserve_stipend,
    resolve_google_api_key,
    stipend_status,
)
from legends_dataforseo import google_stipend


class GoogleStipendLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        google_stipend.clear_disabled_services()
        self._tmp = tempfile.TemporaryDirectory()
        self.ledger_file = Path(self._tmp.name) / "google-stipend-ledger.json"

    def tearDown(self) -> None:
        google_stipend.clear_disabled_services()
        self._tmp.cleanup()

    def test_sku_definitions_and_hard_caps(self) -> None:
        expected = {
            "places_ids_only": ("unlimited", None, None, 0.0),
            "places_essentials": ("monthly", 10000, 8000, 5.0),
            "places_pro": ("monthly", 5000, 4000, 32.0),
            "places_enterprise": ("monthly", 1000, 800, 35.0),
            "geocoding": ("monthly", 10000, 8000, 5.0),
            "map_tiles_2d": ("monthly", 100000, 85000, 0.60),
            "static_maps": ("monthly", 10000, 8000, 2.0),
            "pagespeed_insights": ("daily", 25000, 20000, 0.0),
            "custom_search": ("daily", 100, 85, 5.0),
        }
        for sku, (period, free_limit, hard_cap, overage) in expected.items():
            self.assertIn(sku, STIPEND_SKUS)
            meta = STIPEND_SKUS[sku]
            self.assertEqual(meta["period"], period)
            self.assertEqual(meta["free_limit"], free_limit)
            self.assertEqual(meta["hard_cap"], hard_cap)
            self.assertAlmostEqual(float(meta["overage_usd_per_1k"]), overage)

    def test_hard_cap_enforcement_across_all_skus(self) -> None:
        now = "2026-11-11T12:00:00Z"
        # Unlimited SKU always succeeds
        ok_unlim, rec_unlim = reserve_stipend(
            "places_ids_only", count=500000, ledger_path=self.ledger_file, now=now
        )
        self.assertTrue(ok_unlim)
        self.assertIsNone(rec_unlim["hard_cap"])
        self.assertIsNone(rec_unlim["remaining"])

        capped_cases = [
            ("places_enterprise", 800),
            ("places_pro", 4000),
            ("places_essentials", 8000),
            ("geocoding", 8000),
            ("map_tiles_2d", 85000),
            ("static_maps", 8000),
            ("custom_search", 85),
            ("pagespeed_insights", 20000),
        ]
        for sku, cap in capped_cases:
            self.assertTrue(can_use_stipend(sku, count=cap, ledger_path=self.ledger_file, now=now))
            allowed, receipt = reserve_stipend(sku, count=cap, ledger_path=self.ledger_file, now=now)
            self.assertTrue(allowed, f"Expected {sku} at exact hard_cap={cap} to be allowed")
            self.assertEqual(receipt["used"], cap)
            self.assertEqual(receipt["remaining"], 0)
            self.assertEqual(receipt["cost_usd"], 0.0)

            # Next single call must be refused before any network request
            self.assertFalse(can_use_stipend(sku, count=1, ledger_path=self.ledger_file, now=now))
            allowed_over, receipt_over = check_and_reserve(
                sku, count=1, ledger_path=self.ledger_file, now=now
            )
            self.assertFalse(allowed_over, f"Expected {sku} above hard_cap={cap} to be refused")
            self.assertEqual(receipt_over["used"], cap)
            self.assertEqual(receipt_over["remaining"], 0)
            self.assertEqual(receipt_over["reason"], "stipend_hard_cap_reached")

    def test_daily_and_monthly_period_rollover(self) -> None:
        day_one = "2026-11-11T23:59:00Z"
        day_two = "2026-11-12T00:01:00Z"
        next_month = "2026-12-01T00:00:00Z"

        # Exhaust daily custom_search (85) and monthly places_enterprise (800) on 2026-11-11
        ok_cse, _ = reserve_stipend("custom_search", count=85, ledger_path=self.ledger_file, now=day_one)
        ok_ent, _ = reserve_stipend("places_enterprise", count=800, ledger_path=self.ledger_file, now=day_one)
        self.assertTrue(ok_cse)
        self.assertTrue(ok_ent)
        self.assertFalse(can_use_stipend("custom_search", count=1, ledger_path=self.ledger_file, now=day_one))
        self.assertFalse(can_use_stipend("places_enterprise", count=1, ledger_path=self.ledger_file, now=day_one))

        # On 2026-11-12: daily custom_search resets to 0, but monthly places_enterprise remains at 800
        status_day_two = stipend_status(ledger_path=self.ledger_file, now=day_two)
        self.assertEqual(status_day_two["day_key"], "2026-11-12")
        self.assertEqual(status_day_two["month_key"], "2026-11")
        self.assertEqual(status_day_two["skus"]["custom_search"]["used"], 0)
        self.assertTrue(status_day_two["skus"]["custom_search"]["available"])
        self.assertEqual(status_day_two["skus"]["places_enterprise"]["used"], 800)
        self.assertFalse(status_day_two["skus"]["places_enterprise"]["available"])

        # On 2026-12-01: monthly places_enterprise rolls over to 0
        status_dec = stipend_status(ledger_path=self.ledger_file, now=next_month)
        self.assertEqual(status_dec["month_key"], "2026-12")
        self.assertEqual(status_dec["skus"]["places_enterprise"]["used"], 0)
        self.assertEqual(status_dec["skus"]["places_enterprise"]["remaining"], 800)
        self.assertTrue(status_dec["skus"]["places_enterprise"]["available"])

    def test_key_redaction_strips_aiza_and_explicit_secrets(self) -> None:
        fake_aiza = "AIza" + "SyDummyTestKeyForRedaction987654321"
        raw_url = f"https://www.googleapis.com/customsearch/v1?key={fake_aiza}&q=seo"
        sanitized = redact_google_key(raw_url)
        self.assertNotIn(fake_aiza, sanitized)
        self.assertNotIn("AIza", sanitized)
        self.assertIn("[REDACTED_GOOGLE_KEY]", sanitized)

        custom_secret = "my-custom-secret-token-xyz"
        err_msg = f"Request failed for header X-Goog-Api-Key={custom_secret}"
        sanitized_custom = redact_google_key(err_msg, key=custom_secret)
        self.assertNotIn(custom_secret, sanitized_custom)
        self.assertIn("[REDACTED_GOOGLE_KEY]", sanitized_custom)

    def test_places_lookup_success_and_zero_http_at_cap(self) -> None:
        fake_key = "AIza" + "SyPlacesUnitTestFakeKey000000001"
        places_payload = {
            "places": [
                {
                    "id": "ChIJ_unit_test_123",
                    "name": "places/ChIJ_unit_test_123",
                    "displayName": {"text": "The Gatti Law Firm"},
                    "formattedAddress": "235 Front St SE, Salem, OR 97301",
                    "location": {"latitude": 44.9412, "longitude": -123.0398},
                    "rating": 4.9,
                    "userRatingCount": 312,
                    "websiteUri": "https://www.gattilaw.com/salem/",
                    "nationalPhoneNumber": "(503) 581-2222",
                    "primaryType": "lawyer",
                    "googleMapsUri": "https://maps.google.com/?cid=1234567890123456789",
                }
            ]
        }
        calls: list[ Any ] = []

        def fake_open(req, timeout):
            calls.append((req, timeout))
            return io.BytesIO(json.dumps(places_payload).encode("utf-8"))

        with patch.object(google_stipend, "_google_open", side_effect=fake_open):
            res = google_places_lookup(
                "The Gatti Law Firm Salem",
                tier="enterprise",
                api_key=fake_key,
                ledger_path=self.ledger_file,
                now="2026-11-11",
            )
            self.assertTrue(res["ok"])
            self.assertFalse(res["fallback_recommended"])
            self.assertEqual(res["cost_usd"], 0.0)
            self.assertEqual(res["source"], "google-places-free-stipend")
            self.assertEqual(len(calls), 1)
            self.assertIn("places.rating", calls[0][0].get_header("X-goog-fieldmask"))

            profile = res["profile"]
            self.assertEqual(profile["place_id"], "ChIJ_unit_test_123")
            self.assertEqual(profile["cid"], "1234567890123456789")
            self.assertEqual(profile["title"], "The Gatti Law Firm")
            self.assertEqual(profile["domain"], "gattilaw.com")
            self.assertEqual(profile["rating"], 4.9)
            self.assertEqual(profile["votes_count"], 312)

            # Now reserve the remaining 799 enterprise calls to hit the 800 cap
            reserve_stipend("places_enterprise", count=799, ledger_path=self.ledger_file, now="2026-11-11")
            calls.clear()
            capped_res = google_places_lookup(
                "Another Query",
                tier="enterprise",
                api_key=fake_key,
                ledger_path=self.ledger_file,
                now="2026-11-11",
            )
            self.assertFalse(capped_res["ok"])
            self.assertTrue(capped_res["fallback_recommended"])
            self.assertEqual(capped_res["reason"], "stipend_hard_cap_reached")
            # Zero HTTP calls made once hard cap is reached!
            self.assertEqual(len(calls), 0)

    def test_403_service_disabled_circuit_breaker_and_psi_keyless_retry(self) -> None:
        fake_key = "AIza" + "SyRestrictedTileOnlyKey000000002"
        psi_calls: list[str] = []

        psi_response = {
            "lighthouseResult": {
                "categories": {
                    "performance": {"score": 0.94},
                    "seo": {"score": 1.0},
                    "accessibility": {"score": 0.91},
                    "best-practices": {"score": 0.96},
                },
                "audits": {
                    "largest-contentful-paint": {"score": 0.95, "numericValue": 1820.0, "displayValue": "1.8 s"},
                },
            },
            "loadingExperience": {
                "metrics": {
                    "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 1750, "category": "FAST"},
                    "INTERACTION_TO_NEXT_PAINT": {"percentile": 85, "category": "FAST"},
                    "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 4, "category": "FAST"},
                    "FIRST_CONTENTFUL_PAINT_MS": {"percentile": 1100, "category": "FAST"},
                    "EXPERIMENTAL_TIME_TO_FIRST_BYTE": {"percentile": 320, "category": "FAST"},
                }
            },
        }

        def fake_psi_open(req, timeout):
            psi_calls.append(req.full_url)
            if "key=" in req.full_url:
                err_body = json.dumps(
                    {
                        "error": {
                            "code": 403,
                            "message": f"API_KEY_SERVICE_BLOCKED for key {fake_key}",
                            "status": "PERMISSION_DENIED",
                        }
                    }
                ).encode("utf-8")
                raise HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(err_body))
            return io.BytesIO(json.dumps(psi_response).encode("utf-8"))

        with patch.object(google_stipend, "_google_open", side_effect=fake_psi_open):
            res = google_pagespeed_insights(
                "https://www.gattilaw.com",
                strategy="mobile",
                api_key=fake_key,
                ledger_path=self.ledger_file,
                now="2026-11-11",
            )
            self.assertTrue(res["ok"])
            self.assertTrue(res["keyless"])
            self.assertEqual(res["cost_usd"], 0.0)
            self.assertEqual(res["scores"]["performance"], 94)
            self.assertEqual(res["scores"]["seo"], 100)
            self.assertEqual(res["crux"]["LCP"]["percentile"], 1750)
            self.assertEqual(res["crux"]["INP"]["percentile"], 85)
            # First call used key and got 403, second call retried keyless
            self.assertEqual(len(psi_calls), 2)
            self.assertIn("key=", psi_calls[0])
            self.assertNotIn("key=", psi_calls[1])
            self.assertTrue(google_stipend.is_service_disabled(fake_key, "pagespeedonline"))

            # Subsequent PSI call with the same restricted key skips the keyed attempt and goes straight to keyless!
            psi_calls.clear()
            res2 = google_pagespeed_insights(
                "https://www.gattilaw.com",
                strategy="desktop",
                api_key=fake_key,
                ledger_path=self.ledger_file,
                now="2026-11-11",
            )
            self.assertTrue(res2["ok"])
            self.assertEqual(len(psi_calls), 1)
            self.assertNotIn("key=", psi_calls[0])

    def test_places_and_cse_403_redact_key_and_fallback_to_dataforseo(self) -> None:
        fake_key = "AIza" + "SyBlockedPlacesAndCseKey0000003"
        google_calls: list[str] = []

        def fake_blocked_google(req, timeout):
            google_calls.append(req.full_url)
            err_body = f'{{"error":{{"code":403,"status":"SERVICE_DISABLED","message":"Key {fake_key} blocked"}}}}'.encode(
                "utf-8"
            )
            raise HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(err_body))

        dfs_business_envelope = {
            "status_code": 20000,
            "cost": 0.0015,
            "tasks": [
                {
                    "id": "dfs-biz-1",
                    "status_code": 20000,
                    "result": [{"items": [{"title": "The Gatti Law Firm", "cid": "3865327066180748277"}]}],
                }
            ],
        }

        with (
            patch.object(google_stipend, "_google_open", side_effect=fake_blocked_google),
            patch.object(
                client,
                "_open",
                side_effect=lambda req, timeout: io.BytesIO(json.dumps(dfs_business_envelope).encode("utf-8")),
            ),
        ):
            # 1. Direct google_places_lookup returns ok=False with redacted key and marks 'places' disabled
            places_fail = google_places_lookup(
                "The Gatti Law Firm",
                api_key=fake_key,
                ledger_path=self.ledger_file,
            )
            self.assertFalse(places_fail["ok"])
            self.assertTrue(places_fail["fallback_recommended"])
            self.assertNotIn(fake_key, places_fail["reason"])
            self.assertIn("[REDACTED_GOOGLE_KEY]", places_fail["reason"])
            self.assertEqual(len(google_calls), 1)

            # 2. Hybrid business_lookup_hybrid skips disabled 'places' immediately (0 additional Google HTTP calls)
            # and falls back to DataForSEO business()
            google_calls.clear()
            hybrid_res = business_lookup_hybrid(
                "The Gatti Law Firm",
                api_key=fake_key,
                ledger_path=self.ledger_file,
                credentials=Credentials("dfs-login", "dfs-pass"),
                confirm=True,
                estimated_cost_usd=0.002,
                max_cost_usd=0.01,
            )
            self.assertEqual(len(google_calls), 0)
            self.assertEqual(hybrid_res["status_code"], 20000)
            self.assertEqual(hybrid_res["stipend_fallback_reason"], "service_disabled_in_session")

            # 3. Direct google_custom_search 403 redacts key and marks 'customsearch' disabled
            cse_fail = google_custom_search(
                "portland injury lawyer",
                cx="test-cx-123",
                api_key=fake_key,
                ledger_path=self.ledger_file,
            )
            self.assertFalse(cse_fail["ok"])
            self.assertTrue(cse_fail["fallback_recommended"])
            self.assertNotIn(fake_key, cse_fail["reason"])
            self.assertEqual(len(google_calls), 1)

            # Second CSE call hits in-memory circuit breaker without making an HTTP call
            google_calls.clear()
            cse_second = google_custom_search(
                "portland injury lawyer",
                cx="test-cx-123",
                api_key=fake_key,
                ledger_path=self.ledger_file,
            )
            self.assertFalse(cse_second["ok"])
            self.assertEqual(cse_second["reason"], "service_disabled_in_session")
            self.assertEqual(len(google_calls), 0)

    def test_cli_stipend_and_psi_commands(self) -> None:
        with patch.dict(os.environ, {"LEGENDS_GOOGLE_LEDGER_PATH": str(self.ledger_file)}, clear=False):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["stipend"])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertIn("skus", payload)
            self.assertEqual(payload["skus"]["map_tiles_2d"]["hard_cap"], 85000)

            buf2 = io.StringIO()
            with contextlib.redirect_stdout(buf2):
                rc2 = cli.main(["--stipend-status"])
            self.assertEqual(rc2, 0)
            payload2 = json.loads(buf2.getvalue())
            self.assertIn("skus", payload2)

            buf3 = io.StringIO()
            with contextlib.redirect_stdout(buf3):
                rc3 = cli.main(["psi", "--url", "https://example.com", "--preview"])
            self.assertEqual(rc3, 0)
            payload3 = json.loads(buf3.getvalue())
            self.assertTrue(payload3["preview"])
            self.assertEqual(payload3["estimated_cost_usd"], 0.0)


if __name__ == "__main__":
    unittest.main()
