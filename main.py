import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from typing import Dict, List
import uuid
import asyncio
import random
import time

app = FastAPI()

# Игровая карта
MAP_WIDTH = 2000
MAP_HEIGHT = 2000

# Игроки и еда
players: Dict[str, dict] = {}
foods: List[dict] = []
MAX_FOOD = 100

# Подключения
connections: Dict[str, WebSocket] = {}

# Генерация еды
def generate_food():
    return {"x": random.randint(0, MAP_WIDTH), "y": random.randint(0, MAP_HEIGHT)}

async def game_loop():
    while True:
        # Добавление еды
        while len(foods) < MAX_FOOD:
            foods.append(generate_food())

        # Рассылка состояния
        for pid, ws in list(connections.items()):
            try:
                await ws.send_json({
                    "type": "update",
                    "players": players,
                    "foods": foods
                })
            except:
                await disconnect(pid)

        await asyncio.sleep(0.05)  # 20 FPS

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    try:
        # Получение имени
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

        # Обработка сообщений
        while True:
            msg = await websocket.receive_json()
            if msg["type"] == "move" and not players[name]["dead"]:
                players[name]["x"] += msg["dx"]
                players[name]["y"] += msg["dy"]

                # Ограничения по карте
                players[name]["x"] = max(0, min(MAP_WIDTH, players[name]["x"]))
                players[name]["y"] = max(0, min(MAP_HEIGHT, players[name]["y"]))

                # Съедание еды
                eaten = []
                for food in foods:
                    if ((players[name]["x"] - food["x"])**2 + (players[name]["y"] - food["y"])**2)**0.5 < players[name]["r"]:
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
                            await connections[other_name].send_json({
                                "type": "death",
                                "killer": name
                            })

    except WebSocketDisconnect:
        await disconnect(name)

async def disconnect(name):
    if name in connections:
        del connections[name]
    if name in players:
        del players[name]

# Фоновая задача игры
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(game_loop())

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000)
