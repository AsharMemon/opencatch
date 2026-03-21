"""Tile proxy to Martin vector tile server."""
import json

from fastapi import APIRouter, Request, Response
import httpx

from castline.api.config import settings

router = APIRouter()


@router.get("/tiles/{layer}")
@router.get("/tiles/{layer}.json")
async def get_tilejson(layer: str, request: Request):
    """Proxy TileJSON metadata to Martin and rewrite tile URLs to this API."""
    martin_url = f"{settings.martin_url}/{layer}"

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(martin_url, timeout=10.0)
            if resp.status_code != 200:
                return Response(status_code=resp.status_code)

            payload = resp.json()
            api_base = str(request.base_url).rstrip("/")
            payload["tiles"] = [
                f"{api_base}{settings.api_prefix}/tiles/{layer}/{{z}}/{{x}}/{{y}}.pbf"
            ]

            return Response(
                content=json.dumps(payload),
                media_type="application/json",
                headers={
                    "Cache-Control": "public, max-age=3600",
                    "Access-Control-Allow-Origin": "*",
                },
            )
        except httpx.RequestError:
            return Response(status_code=502, content="Tile server unavailable")


@router.get("/tiles/{layer}/{z}/{x}/{y}.pbf")
async def get_tile(layer: str, z: int, x: int, y: int):
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
