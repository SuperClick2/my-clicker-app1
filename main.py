import asyncio
import json
import websockets

players = {}  # { name: {"x": ..., "y": ..., "color": ...} }

WIDTH, HEIGHT = 800, 600

async def handler(websocket):
    global players
    name = await websocket.recv()
    if name in players:
        await websocket.send(json.dumps({"error": "Имя уже занято"}))
        return

    players[name] = {"x": WIDTH // 2, "y": HEIGHT // 2, "color": [255, 0, 0]}
    await websocket.send(json.dumps({"ok": True}))
    try:
        async for message in websocket:
            data = json.loads(message)
            if "x" in data and "y" in data:
                players[name]["x"] = max(0, min(WIDTH, data["x"]))
                players[name]["y"] = max(0, min(HEIGHT, data["y"]))
            await websocket.send(json.dumps(players))
    except:
        pass
    finally:
        del players[name]

async def main():
    async with websockets.serve(handler, "localhost", 8765):
        print("Server started on ws://localhost:8765")
        await asyncio.Future()  # run forever

asyncio.run(main())
