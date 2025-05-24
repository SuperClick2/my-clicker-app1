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
MASS_LOSS_THRESHOLD = 150  # Начинаем терять массу при 150
MASS_LOSS_INTERVAL = 1  # Каждую секунду
MIN_MASS_LOSS = 2  # Минимальная потеря массы
MAX_MASS_LOSS = 5  # Максимальная потеря массы
MASS_PORTAL_BONUS = 40
MAX_PLAYER_MASS_FOR_PORTAL = 150
BOT_NAMES = ["Bot_Alpha", "Bot_Beta", "Bot_Gamma", "Bot_Delta", "Bot_Epsilon"]
BOT_COUNT = 5
BOT_UPDATE_INTERVAL = 0.1
BOT_SPEED = 5

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

def calculate_mass_loss(current_mass):
    # Чем больше масса, тем больше потеря (от MIN_MASS_LOSS до MAX_MASS_LOSS)
    loss = MIN_MASS_LOSS + (current_mass - MASS_LOSS_THRESHOLD) / 100
    return min(MAX_MASS_LOSS, max(MIN_MASS_LOSS, loss))

async def bot_behavior():
    while True:
        for bot_name, bot in list(bots.items()):
            if bot["dead"]:
                continue
                
            closest_target = None
            min_dist = float('inf')
            is_food = True
            
            for target in {**players, **bots}.values():
                if target["name"] != bot_name and not target["dead"]:
                    dist = ((bot["x"] - target["x"])**2 + (bot["y"] - target["y"])**2)**0.5
                    
                    if (target["r"] < bot["r"] - 5 and dist < min_dist) or \
                       (target["r"] < bot["r"] - 5 and dist < 300):
                        closest_target = target
                        min_dist = dist
                        is_food = False
                    elif target["r"] > bot["r"] + 5 and dist < 200:
                        closest_target = target
                        min_dist = dist
                        is_food = False
                        break
            
            if closest_target is None or is_food:
                for food in foods:
                    dist = ((bot["x"] - food["x"])**2 + (bot["y"] - food["y"])**2)**0.5
                    if dist < min_dist:
                        min_dist = dist
                        closest_target = food
                        is_food = True
            
            if closest_target:
                dx, dy = 0, 0
                if is_food:
                    dx = closest_target["x"] - bot["x"]
                    dy = closest_target["y"] - bot["y"]
                else:
                    if closest_target["r"] < bot["r"] - 5:
                        dx = closest_target["x"] - bot["x"]
                        dy = closest_target["y"] - bot["y"]
                    else:
                        dx = bot["x"] - closest_target["x"]
                        dy = bot["y"] - closest_target["y"]
                
                dist = (dx**2 + dy**2)**0.5
                if dist > 0:
                    dx = dx / dist * BOT_SPEED
                    dy = dy / dist * BOT_SPEED
                
                bot["x"] += dx
                bot["y"] += dy
                
                bot["x"] = max(0, min(MAP_WIDTH, bot["x"]))
                bot["y"] = max(0, min(MAP_HEIGHT, bot["y"]))
                
                if is_food and min_dist < bot["r"]:
                    foods.remove(closest_target)
                    bot["r"] += 1
                elif not is_food and min_dist < bot["r"] and closest_target["r"] < bot["r"] - 5:
                    if closest_target["name"] in bots:
                        del bots[closest_target["name"]]
                        new_bot_name = random.choice(BOT_NAMES)
                        bots[new_bot_name] = {
                            "id": str(uuid.uuid4()),
                            "x": random.randint(0, MAP_WIDTH),
                            "y": random.randint(0, MAP_HEIGHT),
                            "r": 10,
                            "name": new_bot_name,
                            "dead": False,
                            "mass_loss_timer": 0,
                            "bot": True
                        }
                    else:
                        players[closest_target["name"]]["dead"] = True
                        try:
                            await connections[closest_target["name"]].send_json({
                                "type": "death",
                                "killer": bot_name
                            })
                        except:
                            pass
                    
                    bot["r"] += int(closest_target["r"] * 0.6)
                    
                    for ws_name, ws_conn in list(connections.items()):
                        try:
                            await ws_conn.send_json({
                                "type": "eat",
                                "eater": bot_name,
                                "eaten": closest_target["name"]
                            })
                        except:
                            pass

        await asyncio.sleep(BOT_UPDATE_INTERVAL)

async def game_loop():
    last_portal_spawn = datetime.now()
    while True:
        while len(foods) < MAX_FOOD:
            foods.append(generate_food())

        current_time = datetime.now()
        if (current_time - last_portal_spawn).total_seconds() > 10 and len(portals) < MAX_PORTALS:
            portals.append(generate_portal())
            last_portal_spawn = current_time

        # Потеря массы для больших игроков и ботов
        for entity in list(players.values()) + list(bots.values()):
            if entity["r"] >= MASS_LOSS_THRESHOLD:
                now = datetime.now().timestamp()
                if "mass_loss_timer" not in entity or now - entity["mass_loss_timer"] >= MASS_LOSS_INTERVAL:
                    mass_loss = calculate_mass_loss(entity["r"])
                    entity["r"] = max(10, entity["r"] - mass_loss)
                    entity["mass_loss_timer"] = now

        # Проверка взаимодействия с порталами
        for name, player in list(players.items()):
            if player["dead"]:
                continue
                
            interacted_portals = []
            for portal in portals:
                dist = ((player["x"] - portal["x"])**2 + (player["y"] - portal["y"])**2)**0.5
                if dist < player["r"] + portal["r"]:
                    if player["r"] > MAX_PLAYER_MASS_FOR_PORTAL:
                        continue
                        
                    if portal["type"] == "mass":
                        player["r"] += MASS_PORTAL_BONUS
                        interacted_portals.append(portal)
                    elif portal["type"] == "teleport":
                        player["x"] = random.randint(0, MAP_WIDTH)
                        player["y"] = random.randint(0, MAP_HEIGHT)
                        interacted_portals.append(portal)

            for portal in interacted_portals:
                if portal in portals:
                    portals.remove(portal)

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

                eaten = []
                for food in foods:
                    dist = ((players[name]["x"] - food["x"])**2 + (players[name]["y"] - food["y"])**2)**0.5
                    if dist < players[name]["r"]:
                        players[name]["r"] += 1
                        eaten.append(food)
                for food in eaten:
                    foods.remove(food)

                for other_name, other in list({**players, **bots}.items()):
                    if other_name != name and not other["dead"]:
                        dist = ((players[name]["x"] - other["x"])**2 + (players[name]["y"] - other["y"])**2)**0.5
                        if dist < players[name]["r"] and players[name]["r"] > other["r"] + 5:
                            players[name]["r"] += int(other["r"] * 0.6)

                            if other_name in bots:
                                del bots[other_name]
                                new_bot_name = random.choice(BOT_NAMES)
                                bots[new_bot_name] = {
                                    "id": str(uuid.uuid4()),
                                    "x": random.randint(0, MAP_WIDTH),
                                    "y": random.randint(0, MAP_HEIGHT),
                                    "r": 10,
                                    "name": new_bot_name,
                                    "dead": False,
                                    "mass_loss_timer": 0,
                                    "bot": True
                                }
                            else:
                                players[other_name]["dead"] = True
                                try:
                                    await connections[other_name].send_json({
                                        "type": "death",
                                        "killer": name
                                    })
                                except:
                                    pass

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
