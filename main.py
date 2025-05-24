import asyncio
import random
import uuid
from typing import Dict, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from contextlib import asynccontextmanager

MAP_WIDTH = 2000
MAP_HEIGHT = 2000
MAX_FOOD = 100

players: Dict[str, dict] = {}
foods: List[dict] = []
connections: Dict[str, WebSocket] = {}

portals: List[dict] = []  # каждый портал: {"id": uuid, "type": 1 или 2, "x": int, "y": int}
BOT_COUNT = 10
bots: Dict[str, dict] = {}  # боты по имени

# Плавная потеря массы — храним таймер для каждого игрока
mass_loss_timers: Dict[str, float] = {}

def generate_food():
    return {"x": random.randint(0, MAP_WIDTH), "y": random.randint(0, MAP_HEIGHT)}

def generate_portal(portal_type: int):
    return {
        "id": str(uuid.uuid4()),
        "type": portal_type,
        "x": random.randint(50, MAP_WIDTH - 50),
        "y": random.randint(50, MAP_HEIGHT - 50)
    }

def distance(a, b):
    return ((a["x"] - b["x"])**2 + (a["y"] - b["y"])**2)**0.5

async def game_loop():
    global portals

    # Инициализация порталов (2 шт)
    if not portals:
        portals = [generate_portal(1), generate_portal(2)]

    while True:
        # Добавляем еду
        while len(foods) < MAX_FOOD:
            foods.append(generate_food())

        # Боты поведение
        for bot_name, bot in list(bots.items()):
            if bot["dead"]:
                continue
            # Выбираем ближайшего игрока
            if not players:
                # Если нет игроков, боты стоят на месте
                continue

            # Возьмём рандомного игрока для простоты
            target_name, target = random.choice(list(players.items()))

            # Простое движение бота
            speed = 3
            dx = dy = 0
            dist = distance(bot, target)
            if dist == 0:
                continue

            # Если бот меньше игрока — убегает
            if bot["r"] < target["r"]:
                # Двигаемся от игрока
                dx = (bot["x"] - target["x"]) / dist * speed
                dy = (bot["y"] - target["y"]) / dist * speed
            else:
                # Если бот больше — охотится на игрока
                dx = (target["x"] - bot["x"]) / dist * speed
                dy = (target["y"] - bot["y"]) / dist * speed

            bot["x"] += dx
            bot["y"] += dy

            # Ограничения по карте
            bot["x"] = max(0, min(MAP_WIDTH, bot["x"]))
            bot["y"] = max(0, min(MAP_HEIGHT, bot["y"]))

        # Плавная потеря массы у игроков с массой >= 100 (каждые 2 секунды -1)
        now = asyncio.get_event_loop().time()
        for name, p in list(players.items()):
            if p["dead"]:
                continue

            if p["r"] >= 100:
                last_time = mass_loss_timers.get(name, 0)
                if now - last_time > 2:
                    p["r"] -= 1
                    if p["r"] < 10:
                        p["r"] = 10
                    mass_loss_timers[name] = now

        # Отправляем обновления всем
        for name, ws in list(connections.items()):
            try:
                await ws.send_json({
                    "type": "update",
                    "players": players,
                    "foods": foods,
                    "portals": portals,
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

        # Если игрок с таким именем уже есть
        if name in players or name in bots:
            await websocket.send_json({"type": "error", "message": "Имя занято"})
            await websocket.close()
            return

        # Если есть боты, убираем одного, вместо него добавляем игрока
        if bots:
            bot_name, bot = bots.popitem()
            # Можно просто удалить бота
        pid = str(uuid.uuid4())
        players[name] = {
            "id": pid,
            "x": random.randint(0, MAP_WIDTH),
            "y": random.randint(0, MAP_HEIGHT),
            "r": 10,
            "name": name,
            "dead": False,
        }
        connections[name] = websocket

        # Обработка сообщений
        while True:
            msg = await websocket.receive_json()
            if msg["type"] == "move" and not players[name]["dead"]:
                dx, dy = msg["dx"], msg["dy"]
                p = players[name]
                p["x"] += dx
                p["y"] += dy

                # Ограничения карты
                p["x"] = max(0, min(MAP_WIDTH, p["x"]))
                p["y"] = max(0, min(MAP_HEIGHT, p["y"]))

                # Съедание еды (масса растет медленно)
                eaten_food = []
                for food in foods:
                    dist = ((p["x"] - food["x"])**2 + (p["y"] - food["y"])**2)**0.5
                    if dist < p["r"]:
                        p["r"] += 0.2  # медленное увеличение массы от еды
                        eaten_food.append(food)
                for food in eaten_food:
                    foods.remove(food)

                # Съедание игроков
                for other_name, other in list(players.items()):
                    if other_name != name and not other["dead"]:
                        dist = ((p["x"] - other["x"])**2 + (p["y"] - other["y"])**2)**0.5
                        if dist < p["r"] and p["r"] > other["r"] + 5:
                            # Увеличиваем массу на 60% от массы съеденного игрока
                            p["r"] += other["r"] * 0.6
                            other["dead"] = True
                            # Отправляем сообщение о съедании всем
                            for ws_ in connections.values():
                                try:
                                    await ws_.send_json({
                                        "type": "eat",
                                        "eater": name,
                                        "eaten": other_name,
                                    })
                                except:
                                    pass
                            # Удаляем игрока и соединение
                            await disconnect(other_name)

                # Порталы взаимодействие
                used_portal = None
                for portal in portals:
                    dist = ((p["x"] - portal["x"])**2 + (p["y"] - portal["y"])**2)**0.5
                    if dist < p["r"]:
                        used_portal = portal
                        break

                if used_portal:
                    if p["r"] < 150:
                        if used_portal["type"] == 1:
                            # Телепортируем в случайное место
                            p["x"] = random.randint(0, MAP_WIDTH)
                            p["y"] = random.randint(0, MAP_HEIGHT)
                        elif used_portal["type"] == 2:
                            # Добавляем 40 массы
                            p["r"] += 40
                    else:
                        # Масса >= 150 — теряем 30%
                        p["r"] *= 0.7
                        if p["r"] < 10:
                            p["r"] = 10

                    # Удаляем портал после использования
                    portals.remove(used_portal)

                    # Генерируем новый портал того же типа
                    portals.append(generate_portal(used_portal["type"]))

            # Боты атака на игроков — здесь игроки не ходят, поэтому не нужен, бот ходит в game_loop

    except WebSocketDisconnect:
        await disconnect(name)
    except Exception as e:
        print("Ошибка:", e)
        await disconnect(name)


async def disconnect(name):
    if name in connections:
        try:
            await connections[name].close()
        except:
            pass
        del connections[name]
    if name in players:
        del players[name]
    # При выходе игрока — добавляем бота взамен
    if len(bots) < BOT_COUNT:
        bot_id = len(bots) + 1
        bot_name = f"бот{bot_id}"
        bots[bot_name] = {
            "id": str(uuid.uuid4()),
            "x": random.randint(0, MAP_WIDTH),
            "y": random.randint(0, MAP_HEIGHT),
            "r": random.randint(10, 50),
            "name": bot_name,
            "dead": False,
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
