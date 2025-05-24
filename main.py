import asyncio
import random
import uuid
from typing import Dict, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi import Request
from contextlib import asynccontextmanager

# Константы карты
MAP_WIDTH = 2000
MAP_HEIGHT = 2000
MAX_FOOD = 100

players: Dict[str, dict] = {}
foods: List[dict] = []
connections: Dict[str, WebSocket] = {}

# Генерация еды
def generate_food():
    return {"x": random.randint(0, MAP_WIDTH), "y": random.randint(0, MAP_HEIGHT)}

# Фоновая задача игры
async def game_loop():
    while True:
        # Добавление еды
        while len(foods) < MAX_FOOD:
            foods.append(generate_food())

        # Рассылка состояния
        for name, ws in list(connections.items()):
            try:
                await ws.send_json({
                    "type": "update",
                    "players": players,
                    "foods": foods
                })
            except:
                await disconnect(name)

        await asyncio.sleep(0.05)

# Lifespan запускает `game_loop` при старте приложения
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

# Подключение по WebSocket
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        # Ожидаем имя
        data = await websocket.receive_json()
        if data["type"] != "join" or not data["name"]:
            await websocket.close()
            return
        name = data["name"]

        # Имя должно быть уникальным
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

        # Обработка входящих сообщений
        while True:
            msg = await websocket.receive_json()
            if msg["type"] == "move" and not players[name]["dead"]:
                dx, dy = msg["dx"], msg["dy"]
                players[name]["x"] += dx
                players[name]["y"] += dy

                # Ограничения
                players[name]["x"] = max(0, min(MAP_WIDTH, players[name]["x"]))
                players[name]["y"] = max(0, min(MAP_HEIGHT, players[name]["y"]))

                # Съедание еды
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
                            try:
                                await connections[other_name].send_json({
                                    "type": "death",
                                    "killer": name
                                })
                            except:
                                pass

    except WebSocketDisconnect:
        await disconnect(name)
    except Exception as e:
        print("Ошибка в WebSocket:", e)
        await disconnect(name)

# Отключение игрока
async def disconnect(name: str):
    if name in connections:
        del connections[name]
    if name in players:
        del players[name]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)

