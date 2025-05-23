# server.py
import asyncio, json, uuid
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription

peers = {}  # client_id -> {"pc": RTCPeerConnection, "chan": RTCDataChannel}

routes = web.RouteTableDef()

@routes.get("/")
async def index(_):
    return web.Response(text="WebRTC signalling server running")

@routes.post("/join")
async def join(request):
    """
    Создать PeerConnection и вернуть оффер клиенту.
    """
    client_id = str(uuid.uuid4())
    pc = RTCPeerConnection()
    channel = pc.createDataChannel("game")
    peers[client_id] = {"pc": pc, "chan": channel}

    # любое сообщение от клиента → рассылаем всем остальным
    @channel.on("message")
    def on_message(msg):
        for cid, peer in peers.items():
            if cid != client_id and peer["chan"].readyState == "open":
                peer["chan"].send(msg)

    # создаём offer
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    # ждём, пока ICE-кандидаты соберутся
    await asyncio.sleep(0.5)

    return web.json_response({
        "id": client_id,
        "offer": {
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type
        }
    })

@routes.post("/answer")
async def answer(request):
    """
    Клиент присылает answer ― фиксируем у себя.
    """
    data = await request.json()
    cid = data["id"]
    ans = data["answer"]
    peer = peers.get(cid)
    if not peer:
        return web.Response(status=404)

    await peer["pc"].setRemoteDescription(
        RTCSessionDescription(sdp=ans["sdp"], type=ans["type"])
    )
    return web.Response(text="ok")

app = web.Application()
app.add_routes(routes)

if __name__ == "__main__":
    web.run_app(app, port=8080)
