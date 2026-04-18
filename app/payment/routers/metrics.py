"""
Prometheus Metrics Endpoint.
"""

from fastapi import APIRouter, Response
from prometheus_client import generate_latest, REGISTRY, CONTENT_TYPE_LATEST

router = APIRouter()


# @router.get(
#     "/metrics",
#     summary="Prometheus metrics",
#     description="Returns Prometheus-format metrics for monitoring.",
#     responses={
#         200: {
#             "description": "Prometheus metrics in text format",
#             "content": {"text/plain": {}},
#         },
#     },
# )
# async def get_metrics():
#     """Return Prometheus metrics."""
#     metrics_output = generate_latest(REGISTRY)
#     return Response(
#         content=metrics_output,
#         media_type=CONTENT_TYPE_LATEST,
#     )