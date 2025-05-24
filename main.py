import asyncio
import random
import uuid
from typing import Dict, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from contextlib import asynccontextmanager

MAP_WIDTH = 2000
MAP_HEIGHT = 2000
MAX_FOOD = 100
MAX_BOTS = 10

players: Dict[str, dict] = {}
foods: List[dict] = []
connections: Dict[str, WebSocket] = {}
portals: List[dict] = []  # портал: {"id": str, "x": int, "y": int, "type": 1 or 2}

bot_names = [f"бот{i}" for i in range(1, MAX_BOTS + 1)]

def generate_food():
    return {"x": random.randint(0, MAP_WIDTH), "y": random.randint(0, MAP_HEIGHT)}

def generate_portal(portal_type):
    return {
        "id": str(uuid.uuid4()),
        "x": random.randint(50, MAP_WIDTH - 50),
        "y": random.randint(50, MAP_HEIGHT - 50),
        "type": portal_type
    }

async def spawn_portals():
    while True:
        # Должно быть всегда 2 портала: один типа 1, другой типа 2
        types_present = [p['type'] for p in portals]
        if 1 not in types_present:
            portals.append(generate_portal(1))
        if 2 not in types_present:
            portals.append(generate_portal(2))
        await asyncio.sleep(5)

def distance(a, b):
    return ((a["x"] - b["x"])**2 + (a["y"] - b["y"])**2)**0.5

async def game_loop():
    while True:
        # Добавляем еду
        while len(foods) < MAX_FOOD:
            foods.append(generate_food())

        # Обновляем ботов (движение)
        update_bots()

        # Снижение массы у игроков с r >= 100 каждые 2 секунды
        now = asyncio.get_event_loop().time()
        for p in players.values():
            if "last_mass_loss" not in p:
                p["last_mass_loss"] = now
            if p["r"] >= 100 and now - p["last_mass_loss"] >= 2:
                p["r"] = max(10, p["r"] - 1)
                p["last_mass_loss"] = now

        # Рассылка состояния
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

def update_bots():
    # Простая логика движения ботов
    for name, bot in players.items():
        if not name.startswith("бот") or bot.get("dead", False):
            continue

        # Найти игрока - цель (например, любой не бот)
        targets = [p for p in players.values() if not p["name"].startswith("бот") and not p.get("dead", False)]
        if not targets:
            continue
        # Возьмём случайного игрока
        target = random.choice(targets)

        dx = bot["x"] - target["x"]
        dy = bot["y"] - target["y"]
        dist = (dx*dx + dy*dy)**0.5

        speed = 3

        if dist == 0:
            continue

        # Если бот меньше игрока - убегает, иначе - охотится
        if bot["r"] < target["r"]:
            # Убегаем от игрока
            bot["x"] += dx / dist * speed
            bot["y"] += dy / dist * speed
        else:
            # Охотимся на игрока
            bot["x"] -= dx / dist * speed
            bot["y"] -= dy / dist * speed

        # Ограничения по карте
        bot["x"] = max(0, min(MAP_WIDTH, bot["x"]))
        bot["y"] = max(0, min(MAP_HEIGHT, bot["y"]))

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Запуск game_loop и spawn_portals при старте сервера
    task1 = asyncio.create_task(game_loop())
    task2 = asyncio.create_task(spawn_portals())
    yield
    task1.cancel()
    task2.cancel()
    try:
        await task1
        await task2
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

        # Если имя занято
        if name in players:
            await websocket.send_json({"type": "error", "message": "Имя занято"})
            await websocket.close()
            return

        # Убрать бота при заходе игрока (если есть)
        bot_to_remove = None
        for bot_name in bot_names:
            if bot_name in players:
                bot_to_remove = bot_name
                break
        if bot_to_remove:
            del players[bot_to_remove]
            if bot_to_remove in connections:
                del connections[bot_to_remove]

        pid = str(uuid.uuid4())
        players[name] = {
            "id": pid,
            "x": random.randint(0, MAP_WIDTH),
            "y": random.randint(0, MAP_HEIGHT),
            "r": 10,
            "name": name,
            "dead": False,
            "last_mass_loss": asyncio.get_event_loop().time()
        }
        connections[name] = websocket

        while True:
            msg = await websocket.receive_json()
            if msg["type"] == "move" and not players[name]["dead"]:
                dx, dy = msg["dx"], msg["dy"]
                p = players[name]
                p["x"] += dx
                p["y"] += dy

                # Ограничения по карте
                p["x"] = max(0, min(MAP_WIDTH, p["x"]))
                p["y"] = max(0, min(MAP_HEIGHT, p["y"]))

                # Проверка на поедание еды
                eaten_foods = []
                for food in foods:
                    dist = distance(p, food)
                    if dist < p["r"]:
                        p["r"] += 1
                        eaten_foods.append(food)
                for food in eaten_foods:
                    foods.remove(food)

                # Проверка на поедание порталов
                used_portals = []
                for portal in portals:
                    dist = distance(p, portal)
                    if dist < p["r"]:
                        if p["r"] < 150:
                            if portal["type"] == 1:
                                # Телепорт в случайную точку карты
                                p["x"] = random.randint(0, MAP_WIDTH)
                                p["y"] = random.randint(0, MAP_HEIGHT)
                            elif portal["type"] == 2:
                                p["r"] += 40
                        else:
                            # Масса больше 150 - теряем 30% массы
                            p["r"] = max(10, int(p["r"] * 0.7))
                        used_portals.append(portal)
                for portal in used_portals:
                    portals.remove(portal)

                # Проверка поедания игроков (включая ботов)
                for other_name, other in list(players.items()):
                    if other_name != name and not other["dead"]:
                        dist = distance(p, other)
                        if dist < p["r"] and p["r"] > other["r"] + 5:
                            # Едим игрока
                            p["r"] += int(other["r"] * 0.6)
                            other["dead"] = True
                            try:
                                await connections[other_name].send_json({
                                    "type": "death",
                                    "killer": name
                                })
                            except:
                                pass
                            # Уведомляем всех о съедании
                            for ws in connections.values():
                                try:
                                    await ws.send_json({
                                        "type": "eat",
                                        "eater": name,
                                        "eaten": other_name
                                    })
                                except:
                                    pass
                            # Удаляем игрока
                            if other_name in connections:
                                await connections[other_name].close()
                                del connections[other_name]
                            del players[other_name]

            elif msg["type"] == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        pass
    finally:
        if name in players:
            del players[name]
        if name in connections:
            del connections[name]

# Инициализация ботов при старте
@app.on_event("startup")
async def startup_event():
    for i in range(MAX_BOTS):
        bot_name = bot_names[i]
        players[bot_name] = {
            "id": str(uuid.uuid4()),
            "x": random.randint(0, MAP_WIDTH),
            "y": random.randint(0, MAP_HEIGHT),
            "r": random.randint(15, 30),
            "name": bot_name,
            "dead": False,
            "last_mass_loss": asyncio.get_event_loop().time()
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
