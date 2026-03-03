"""
Tests for Prometheus Metrics Endpoint

Tests:
    1. GET /metrics → returns text/plain Prometheus format
    2. Contains expected metric names
    3. Accessible without authentication (internal use)
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestMetrics:
    """Test metrics endpoint."""

    URL = "/api/v1/payment/metrics"

    async def test_metrics_returns_prometheus_format(self, client):
        """Metrics endpoint returns text/plain."""
        response = await client.get(self.URL)
        assert response.status_code == 200
        assert "text/plain" in response.headers.get("content-type", "")

    async def test_metrics_contains_expected_names(self, client):
        """Response contains our custom metric names."""
        response = await client.get(self.URL)
        text = response.text

        # Check at least some of our metrics exist
        expected_metrics = [
            "payment_",  # Any payment metric
        ]

        for metric_name in expected_metrics:
            assert metric_name in text, (
                f"Expected metric containing '{metric_name}' not found"
            )

    async def test_metrics_no_auth_required(self, client):
        """Metrics should be accessible without JWT (internal endpoint)."""
        response = await client.get(self.URL)
        assert response.status_code == 200
