import asyncio
import random
import uuid
from typing import Dict, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi import Request
from contextlib import asynccontextmanager

MAP_WIDTH = 2000
MAP_HEIGHT = 2000
MAX_FOOD = 100
MIN_PORTALS = 8
MAX_PORTALS = 13

players: Dict[str, dict] = {}
foods: List[dict] = []
connections: Dict[str, WebSocket] = {}
portals: List[dict] = []

PORTAL_TYPES = ["teleport", "mass"]
PORTAL_RADIUS = 25

# Генерация еды
def generate_food():
    return {"x": random.randint(0, MAP_WIDTH), "y": random.randint(0, MAP_HEIGHT)}

def generate_portal():
    p_type = random.choice(PORTAL_TYPES)
    return {
        "id": str(uuid.uuid4()),
        "x": random.randint(0, MAP_WIDTH),
        "y": random.randint(0, MAP_HEIGHT),
        "type": p_type
    }

async def mass_decay():
    while True:
        await asyncio.sleep(2)
        for p in players.values():
            if not p["dead"] and p["r"] >= 100:
                p["r"] = max(10, p["r"] - 1)

# Фоновая задача игры
async def game_loop():
    asyncio.create_task(mass_decay())
    while True:
        while len(foods) < MAX_FOOD:
            foods.append(generate_food())

        while len(portals) < random.randint(MIN_PORTALS, MAX_PORTALS):
            portals.append(generate_portal())

        for name, ws in list(connections.items()):
            try:
                await ws.send_json({
                    "type": "update",
                    "players": players,
                    "foods": foods,
                    "portals": portals
                })
            except:
                await disconnect(name)

        await asyncio.sleep(0.05)

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(game_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

app = FastAPI(lifespan=lifespan)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        data = await websocket.receive_json()
        if data["type"] != "join" or not data["name"]:
            await websocket.close()
            return
        name = data["name"]
        if name in players:
            await websocket.send_json({"type": "error", "message": "Имя занято"})
            await websocket.close()
            return

        pid = str(uuid.uuid4())
        players[name] = {
            "id": pid,
            "x": random.randint(0, MAP_WIDTH),
            "y": random.randint(0, MAP_HEIGHT),
            "r": 10,
            "name": name,
            "dead": False
        }
        connections[name] = websocket

        while True:
            msg = await websocket.receive_json()
            if msg["type"] == "move" and not players[name]["dead"]:
                dx, dy = msg["dx"], msg["dy"]
                players[name]["x"] += dx
                players[name]["y"] += dy

                players[name]["x"] = max(0, min(MAP_WIDTH, players[name]["x"]))
                players[name]["y"] = max(0, min(MAP_HEIGHT, players[name]["y"]))

                eaten = []
                for food in foods:
                    dist = ((players[name]["x"] - food["x"])**2 + (players[name]["y"] - food["y"])**2)**0.5
                    if dist < players[name]["r"]:
                        players[name]["r"] += 1
                        eaten.append(food)
                for food in eaten:
                    foods.remove(food)

                # Съедание игроков
                for other_name, other in list(players.items()):
                    if other_name != name and not other["dead"]:
                        dist = ((players[name]["x"] - other["x"])**2 + (players[name]["y"] - other["y"])**2)**0.5
                        if dist < players[name]["r"] and players[name]["r"] > other["r"] + 5:
                            other["dead"] = True
                            gain = int(other["r"] * 0.6)
                            players[name]["r"] += gain
                            try:
                                await connections[other_name].send_json({
                                    "type": "death",
                                    "killer": name
                                })
                            except:
                                pass
                            try:
                                await connections[name].send_json({
                                    "type": "kill_msg",
                                    "target": other_name
                                })
                            except:
                                pass

                used_portals = []
                for portal in portals:
                    dist = ((players[name]["x"] - portal["x"])**2 + (players[name]["y"] - portal["y"])**2)**0.5
                    if dist < PORTAL_RADIUS:
                        if portal["type"] == "teleport":
                            if players[name]["r"] < 150:
                                players[name]["x"] = random.randint(0, MAP_WIDTH)
                                players[name]["y"] = random.randint(0, MAP_HEIGHT)
                            else:
                                players[name]["r"] = int(players[name]["r"] * 0.7)
                            used_portals.append(portal)
                        elif portal["type"] == "mass":
                            if players[name]["r"] < 150:
                                players[name]["r"] += 40
                                used_portals.append(portal)
                            else:
                                players[name]["r"] = int(players[name]["r"] * 0.7)
                                used_portals.append(portal)
                for portal in used_portals:
                    portals.remove(portal)

    except WebSocketDisconnect:
        await disconnect(name)
    except Exception as e:
        print("Ошибка в WebSocket:", e)
        await disconnect(name)

async def disconnect(name: str):
    if name in connections:
        del connections[name]
    if name in players:
        del players[name]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
