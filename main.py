from aiohttp import web
import asyncio
import json

routes = web.RouteTableDef()
peers = {}

@routes.post("/offer")
async def offer(request):
    data = await request.json()
    peer_id = data["id"]
    peers[peer_id] = data["offer"]
    return web.Response(text="OK")

@routes.get("/offer/{peer_id}")
async def get_offer(request):
    peer_id = request.match_info["peer_id"]
    if peer_id in peers:
        return web.json_response({"offer": peers.pop(peer_id)})
    return web.Response(status=404)

app = web.Application()
app.add_routes(routes)
web.run_app(app, port=8080)
