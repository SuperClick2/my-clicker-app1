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
BOT_NAMES = ["Bot_Alpha", "Bot_Beta", "Bot_Gamma", "Bot_Delta", "Bot_Epsilon"]
BOT_COUNT = 5
BOT_UPDATE_INTERVAL = 0.5  # секунды

players: Dict[str, dict] = {}
foods: List[dict] = []
portals: List[dict] = []
connections: Dict[str, WebSocket] = {}
bots: Dict[str, dict] = {}

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

async def bot_behavior():
    while True:
        for bot_name, bot in list(bots.items()):
            if bot["dead"]:
                continue
                
            # Простой ИИ ботов: двигаться к ближайшей еде или игроку меньше себя
            closest_food = None
            min_dist = float('inf')
            
            # Ищем ближайшую еду
            for food in foods:
                dist = ((bot["x"] - food["x"])**2 + (bot["y"] - food["y"])**2)**0.5
                if dist < min_dist:
                    min_dist = dist
                    closest_food = food
            
            # Ищем ближайшего игрока, которого можно съесть
            closest_player = None
            player_min_dist = float('inf')
            for player_name, player in players.items():
                if player_name != bot_name and not player["dead"] and bot["r"] > player["r"] + 5:
                    dist = ((bot["x"] - player["x"])**2 + (bot["y"] - player["y"])**2)**0.5
                    if dist < player_min_dist:
                        player_min_dist = dist
                        closest_player = player
            
            # Выбираем цель: либо игрока, либо еду
            target = closest_player if closest_player and player_min_dist < 300 else closest_food
            
            if target:
                # Двигаемся к цели
                dx = target["x"] - bot["x"]
                dy = target["y"] - bot["y"]
                dist = (dx**2 + dy**2)**0.5
                if dist > 0:
                    dx = dx / dist * 3
                    dy = dy / dist * 3
                
                bot["x"] += dx
                bot["y"] += dy
                
                # Ограничиваем движение в пределах карты
                bot["x"] = max(0, min(MAP_WIDTH, bot["x"]))
                bot["y"] = max(0, min(MAP_HEIGHT, bot["y"]))
                
                # Съедание еды
                eaten = []
                for food in foods:
                    dist = ((bot["x"] - food["x"])**2 + (bot["y"] - food["y"])**2)**0.5
                    if dist < bot["r"]:
                        bot["r"] += 1
                        eaten.append(food)
                for food in eaten:
                    foods.remove(food)
                
                # Съедание игроков
                for player_name, player in list(players.items()):
                    if player_name != bot_name and not player["dead"]:
                        dist = ((bot["x"] - player["x"])**2 + (bot["y"] - player["y"])**2)**0.5
                        if dist < bot["r"] and bot["r"] > player["r"] + 5:
                            # Увеличиваем радиус бота
                            bot["r"] += int(player["r"] * 0.6)
                            
                            # Удаляем жертву
                            players[player_name]["dead"] = True
                            try:
                                await connections[player_name].send_json({
                                    "type": "death",
                                    "killer": bot_name
                                })
                            except:
                                pass
                            
                            # Сообщаем всем о съедании
                            for ws_name, ws_conn in list(connections.items()):
                                try:
                                    await ws_conn.send_json({
                                        "type": "eat",
                                        "eater": bot_name,
                                        "eaten": player_name
                                    })
                                except:
                                    pass
                
                # Взаимодействие с порталами (боты не используют порталы)
                
        await asyncio.sleep(BOT_UPDATE_INTERVAL)

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
                    # Игроки с массой > 150 не могут использовать порталы
                    if player["r"] > MAX_PLAYER_MASS_FOR_PORTAL:
                        continue
                        
                    if portal["type"] == "mass":
                        player["r"] += MASS_PORTAL_BONUS
                        interacted_portals.append(portal)
                    elif portal["type"] == "teleport":
                        # Телепортация
                        player["x"] = random.randint(0, MAP_WIDTH)
                        player["y"] = random.randint(0, MAP_HEIGHT)
                        interacted_portals.append(portal)

            # Удаляем использованные порталы
            for portal in interacted_portals:
                if portal in portals:
                    portals.remove(portal)

        # Отправка обновлений всем игрокам
        all_players = {**players, **bots}
        for name, ws in list(connections.items()):
            try:
                await ws.send_json({
                    "type": "update",
                    "players": all_players,
                    "foods": foods,
                    "portals": portals
                })
            except:
                await disconnect(name)

        await asyncio.sleep(0.05)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Создаем ботов при запуске
    for i in range(BOT_COUNT):
        bot_name = BOT_NAMES[i] if i < len(BOT_NAMES) else f"Bot_{i+1}"
        bots[bot_name] = {
            "id": str(uuid.uuid4()),
            "x": random.randint(0, MAP_WIDTH),
            "y": random.randint(0, MAP_HEIGHT),
            "r": random.randint(15, 30),
            "name": bot_name,
            "dead": False,
            "mass_loss_timer": 0,
            "bot": True
        }
    
    game_task = asyncio.create_task(game_loop())
    bot_task = asyncio.create_task(bot_behavior())
    yield
    game_task.cancel()
    bot_task.cancel()
    try:
        await game_task
        await bot_task
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

        if name in players or name in bots:
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

                # Съедание игроков и ботов
                for other_name, other in list({**players, **bots}.items()):
                    if other_name != name and not other["dead"]:
                        dist = ((players[name]["x"] - other["x"])**2 + (players[name]["y"] - other["y"])**2)**0.5
                        if dist < players[name]["r"] and players[name]["r"] > other["r"] + 5:
                            # Увеличиваем радиус убийцы на 60% радиуса жертвы
                            players[name]["r"] += int(other["r"] * 0.6)

                            if other_name in bots:
                                # Удаляем бота и создаем нового
                                del bots[other_name]
                                new_bot_name = random.choice(BOT_NAMES)
                                bots[new_bot_name] = {
                                    "id": str(uuid.uuid4()),
                                    "x": random.randint(0, MAP_WIDTH),
                                    "y": random.randint(0, MAP_HEIGHT),
                                    "r": random.randint(15, 30),
                                    "name": new_bot_name,
                                    "dead": False,
                                    "mass_loss_timer": 0,
                                    "bot": True
                                }
                            else:
                                # Удаляем игрока
                                players[other_name]["dead"] = True
                                try:
                                    await connections[other_name].send_json({
                                        "type": "death",
                                        "killer": name
                                    })
                                except:
                                    pass

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
