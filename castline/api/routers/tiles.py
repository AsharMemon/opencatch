"""Tile proxy to Martin vector tile server."""
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
import httpx

from castline.api.config import settings

router = APIRouter()


@router.get("/tiles/{layer}/{z}/{x}/{y}.pbf")
async def get_tile(layer: str, z: int, x: int, y: int, request: Request):
    """Proxy vector tile requests to Martin tile server."""
    martin_url = f"{settings.martin_url}/{layer}/{z}/{x}/{y}"

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(martin_url, timeout=10.0)
            if resp.status_code == 200:
                return Response(
                    content=resp.content,
                    media_type="application/x-protobuf",
                    headers={
                        "Cache-Control": "public, max-age=86400",
                        "Access-Control-Allow-Origin": "*",
                    },
                )
            return Response(status_code=resp.status_code)
        except httpx.RequestError:
            return Response(status_code=502, content="Tile server unavailable")
