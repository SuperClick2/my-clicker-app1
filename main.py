import asyncio
import random
import uuid
from typing import Dict, List
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from contextlib import asynccontextmanager

MAP_WIDTH = 2000
MAP_HEIGHT = 2000
MAX_FOOD = 100
MAX_PORTALS = 10
MIN_PORTALS = 8
PORTAL_RADIUS = 20
MASS_LOSS_THRESHOLD = 100
MASS_LOSS_INTERVAL = 2  # секунды
MASS_LOSS_AMOUNT = 1
MASS_PORTAL_BONUS = 40
TELEPORT_PENALTY = 0.3  # 30% потери массы при телепортации если масса > 150
MAX_PLAYER_MASS_FOR_PORTAL = 150

players: Dict[str, dict] = {}
foods: List[dict] = []
portals: List[dict] = []
connections: Dict[str, WebSocket] = {}

def generate_food():
    return {"x": random.randint(0, MAP_WIDTH), "y": random.randint(0, MAP_HEIGHT)}

def generate_portal():
    portal_type = random.choice(["mass", "teleport"])
    return {
        "x": random.randint(0, MAP_WIDTH),
        "y": random.randint(0, MAP_HEIGHT),
        "r": PORTAL_RADIUS,
        "type": portal_type,
        "id": str(uuid.uuid4())
    }

async def game_loop():
    last_portal_spawn = datetime.now()
    while True:
        # Генерация еды
        while len(foods) < MAX_FOOD:
            foods.append(generate_food())

        # Генерация порталов
        current_time = datetime.now()
        if (current_time - last_portal_spawn).total_seconds() > 10 and len(portals) < MAX_PORTALS:
            portals.append(generate_portal())
            last_portal_spawn = current_time

        # Потеря массы для больших игроков
        for name, player in list(players.items()):
            if player["r"] >= MASS_LOSS_THRESHOLD:
                now = datetime.now().timestamp()
                if "mass_loss_timer" not in player or now - player["mass_loss_timer"] >= MASS_LOSS_INTERVAL:
                    player["r"] = max(10, player["r"] - MASS_LOSS_AMOUNT)
                    player["mass_loss_timer"] = now

        # Проверка взаимодействия с порталами
        for name, player in list(players.items()):
            if player["dead"]:
                continue
                
            interacted_portals = []
            for portal in portals:
                dist = ((player["x"] - portal["x"])**2 + (player["y"] - portal["y"])**2)**0.5
                if dist < player["r"] + portal["r"]:
                    if portal["type"] == "mass" and player["r"] < MAX_PLAYER_MASS_FOR_PORTAL:
                        player["r"] += MASS_PORTAL_BONUS
                        interacted_portals.append(portal)
                    elif portal["type"] == "teleport":
                        if player["r"] <= MAX_PLAYER_MASS_FOR_PORTAL:
                            # Телепортация
                            player["x"] = random.randint(0, MAP_WIDTH)
                            player["y"] = random.randint(0, MAP_HEIGHT)
                            interacted_portals.append(portal)
                        else:
                            # Штраф за использование при большой массе
                            player["r"] = max(10, int(player["r"] * (1 - TELEPORT_PENALTY)))
                            try:
                                await connections[name].send_json({
                                    "type": "message",
                                    "text": f"Штраф за телепорт! Потеряно {TELEPORT_PENALTY*100}% массы"
                                })
                            except:
                                pass

            # Удаляем использованные порталы
            for portal in interacted_portals:
                if portal in portals:
                    portals.remove(portal)

        # Отправка обновлений всем игрокам
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
    name = None
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
            "dead": False,
            "mass_loss_timer": 0
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
                            # Увеличиваем радиус убийцы на 60% радиуса жертвы
                            players[name]["r"] += int(other["r"] * 0.6)

                            try:
                                await connections[other_name].send_json({
                                    "type": "death",
                                    "killer": name
                                })
                            except:
                                pass

                            # Удаляем жертву
                            del players[other_name]
                            try:
                                await connections[other_name].close()
                            except:
                                pass
                            if other_name in connections:
                                del connections[other_name]

                            # Сообщаем всем о съедании
                            for ws_name, ws_conn in list(connections.items()):
                                try:
                                    await ws_conn.send_json({
                                        "type": "eat",
                                        "eater": name,
                                        "eaten": other_name
                                    })
                                except:
                                    pass

    except WebSocketDisconnect:
        if name:
            await disconnect(name)
    except Exception as e:
        print("Ошибка в WebSocket:", e)
        if name:
            await disconnect(name)

async def disconnect(name: str):
    if name in connections:
        del connections[name]
    if name in players:
        del players[name]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
