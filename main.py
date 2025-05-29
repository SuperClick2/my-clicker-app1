import asyncio
import random
import uuid
from typing import Dict, List
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from contextlib import asynccontextmanager
import hashlib
import secrets
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

# Конфигурация игры
MAP_WIDTH, MAP_HEIGHT = 5000, 5000  # Увеличена карта
MAX_FOOD = 500
MAX_PORTALS = 20
MIN_PORTALS = 10
PORTAL_RADIUS = 20
MASS_LOSS_THRESHOLD = 150
MASS_LOSS_INTERVAL = 1
MIN_MASS_LOSS = 2
MAX_MASS_LOSS = 5
MASS_PORTAL_BONUS = 40
MAX_PLAYER_MASS_FOR_PORTAL = 150
MAX_PLAYER_MASS = 1000
BOT_NAMES = ["Bot_Alpha", "Bot_Beta", "Bot_Gamma", "Bot_Delta", "Bot_Epsilon",
             "Bot_Zeta", "Bot_Eta", "Bot_Theta", "Bot_Iota", "Bot_Kappa"]
BOT_COUNT = 10
BOT_UPDATE_INTERVAL = 0.08
BASE_SPEED = 5
BOT_RESPAWN_TIME = 9
MAX_NAME_LENGTH = 15
MAX_CONNECTIONS = 100
CONNECTION_RATE_LIMIT = 5
MAX_CHAT_MESSAGES = 100  # Максимальное количество сообщений в чате
CHAT_MESSAGE_LENGTH = 200  # Максимальная длина сообщения

# Состояние игры
players: Dict[str, dict] = {}
foods: List[dict] = []
portals: List[dict] = []
connections: Dict[str, WebSocket] = {}
bots: Dict[str, dict] = {}
bot_respawn_tasks: Dict[str, asyncio.Task] = {}
connection_times: List[datetime] = []
chat_messages: List[dict] = []  # Сообщения чата: {"sender": name, "message": text, "color": color}

# Защита от DDoS
def check_rate_limit():
    now = datetime.now()
    connection_times[:] = [t for t in connection_times if (now - t).total_seconds() < 1]
    return len(connection_times) < CONNECTION_RATE_LIMIT

def validate_name(name: str) -> bool:
    if not name or len(name) > MAX_NAME_LENGTH:
        return False
    return all(c.isalnum() or c in ['_', '-'] for c in name)

def validate_color(color: List[int]) -> bool:
    if len(color) != 3:
        return False
    return all(0 <= c <= 255 for c in color)

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
    loss = MIN_MASS_LOSS + (current_mass - MASS_LOSS_THRESHOLD) / 50
    return min(MAX_MASS_LOSS, max(MIN_MASS_LOSS, loss))

def calculate_speed(mass):
    return max(1, BASE_SPEED * (100 / mass)**0.5)

async def respawn_bot(bot_name: str):
    await asyncio.sleep(BOT_RESPAWN_TIME)
    bots[bot_name] = {
        "id": str(uuid.uuid4()),
        "x": random.randint(0, MAP_WIDTH),
        "y": random.randint(0, MAP_HEIGHT),
        "r": random.randint(15, 30),
        "name": bot_name,
        "color": (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)),
        "dead": False,
        "mass_loss_timer": 0,
        "bot": True
    }
    bot_respawn_tasks.pop(bot_name, None)

async def bot_behavior():
    while True:
        for bot_name, bot in list(bots.items()):
            if bot["dead"]:
                continue
                
            bot_speed = calculate_speed(bot["r"])
            closest_target = None
            min_dist = float('inf')
            is_food = True
            
            for target in {**players, **bots}.values():
                if target["name"] != bot_name and not target["dead"]:
                    dist = ((bot["x"] - target["x"])**2 + (bot["y"] - target["y"])**2)**0.5
                    
                    if target["r"] < bot["r"] - 5 and dist < min_dist:
                        closest_target = target
                        min_dist = dist
                        is_food = False
                    elif target["r"] > bot["r"] + 5 and dist < 250:
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
                    dx = dx / dist * bot_speed
                    dy = dy / dist * bot_speed
                
                bot["x"] += dx
                bot["y"] += dy
                bot["x"] = max(0, min(MAP_WIDTH, bot["x"]))
                bot["y"] = max(0, min(MAP_HEIGHT, bot["y"]))
                
                if is_food and min_dist < bot["r"]:
                    foods.remove(closest_target)
                    bot["r"] += 1
                elif not is_food and min_dist < bot["r"] and closest_target["r"] < bot["r"] - 5:
                    eater_name = bot_name
                    eaten_name = closest_target["name"]
                    
                    if eaten_name in bots:
                        bots[eaten_name]["dead"] = True
                        if eaten_name not in bot_respawn_tasks:
                            bot_respawn_tasks[eaten_name] = asyncio.create_task(respawn_bot(eaten_name))
                    elif eaten_name in players:
                        players[eaten_name]["dead"] = True
                        try:
                            await connections[eaten_name].send_json({
                                "type": "death",
                                "killer": eater_name
                            })
                        except:
                            pass
                    
                    bot["r"] += int(closest_target["r"] * 0.6)
                    
                    for ws_name, ws_conn in list(connections.items()):
                        try:
                            await ws_conn.send_json({
                                "type": "eat",
                                "eater": eater_name,
                                "eaten": eaten_name
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

        for entity in list(players.values()) + list(bots.values()):
            if not entity.get("dead", False) and entity["r"] >= MASS_LOSS_THRESHOLD:
                now = datetime.now().timestamp()
                if "mass_loss_timer" not in entity or now - entity["mass_loss_timer"] >= MASS_LOSS_INTERVAL:
                    mass_loss = calculate_mass_loss(entity["r"])
                    entity["r"] = max(10, entity["r"] - mass_loss)
                    entity["mass_loss_timer"] = now

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

        for name, player in list(players.items()):
            if player["r"] > MAX_PLAYER_MASS:
                try:
                    await connections[name].send_json({
                        "type": "error",
                        "message": "Ошибка: ваш размер превысил максимально допустимый!"
                    })
                    await disconnect(name)
                except:
                    pass

        all_players = {k: v for k, v in {**players, **bots}.items() if not v.get("dead", False)}
        for name, ws in list(connections.items()):
            try:
                visible_players = {}
                visible_foods = []
                visible_portals = []
                
                # Фильтрация видимых объектов в зависимости от размера игрока
                view_radius = min(3000, 1000 + players[name]["r"] * 10) if name in players else 1000
                
                player_x = players[name]["x"] if name in players else 0
                player_y = players[name]["y"] if name in players else 0
                
                for p_id, p in all_players.items():
                    dist = ((player_x - p["x"])**2 + (player_y - p["y"])**2)**0.5
                    if dist < view_radius:
                        visible_players[p_id] = p
                
                for food in foods:
                    dist = ((player_x - food["x"])**2 + (player_y - food["y"])**2)**0.5
                    if dist < view_radius:
                        visible_foods.append(food)
                
                for portal in portals:
                    dist = ((player_x - portal["x"])**2 + (player_y - portal["y"])**2)**0.5
                    if dist < view_radius:
                        visible_portals.append(portal)
                
                await ws.send_json({
                    "type": "update",
                    "players": visible_players,
                    "foods": visible_foods,
                    "portals": visible_portals,
                    "chat": chat_messages[-20:],  # Последние 20 сообщений
                    "your_mass": players[name]["r"] if name in players else 0
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
            "color": (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)),
            "dead": False,
            "mass_loss_timer": 0,
            "bot": True
        }
    
    game_task = asyncio.create_task(game_loop())
    bot_task = asyncio.create_task(bot_behavior())
    yield
    game_task.cancel()
    bot_task.cancel()
    for task in bot_respawn_tasks.values():
        task.cancel()
    try:
        await game_task
        await bot_task
    except asyncio.CancelledError:
        pass

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["*"],
)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    if not check_rate_limit():
        await websocket.close()
        return
    
    connection_times.append(datetime.now())
    
    if len(connections) >= MAX_CONNECTIONS:
        await websocket.close()
        return
    
    await websocket.accept()
    name = None
    try:
        data = await websocket.receive_json()
        if data["type"] != "join" or not data.get("name"):
            await websocket.send_json({"type": "error", "message": "Неверный запрос на подключение"})
            await websocket.close()
            return
        
        name = data["name"]
        
        if not validate_name(name):
            await websocket.send_json({
                "type": "error",
                "message": f"Недопустимое имя. Используйте только буквы, цифры и _-. Максимум {MAX_NAME_LENGTH} символов."
            })
            await websocket.close()
            return
        
        if name in players or name in bots:
            await websocket.send_json({
                "type": "error",
                "message": "Это имя уже занято. Пожалуйста, выберите другое."
            })
            await websocket.close()
            return
        
        color = data.get("color", [255, 0, 0])
        if not validate_color(color):
            color = [255, 0, 0]
        
        players[name] = {
            "id": str(uuid.uuid4()),
            "x": random.randint(0, MAP_WIDTH),
            "y": random.randint(0, MAP_HEIGHT),
            "r": 10,
            "name": name,
            "color": color,
            "dead": False,
            "mass_loss_timer": 0
        }
        connections[name] = websocket

        # Отправляем уведомление в чат о новом игроке
        chat_messages.append({
            "sender": "Система",
            "message": f"{name} присоединился к игре!",
            "color": [100, 100, 100]
        })
        if len(chat_messages) > MAX_CHAT_MESSAGES:
            chat_messages.pop(0)

        while True:
            msg = await websocket.receive_json()
            if msg["type"] == "move" and not players[name]["dead"]:
                player_speed = calculate_speed(players[name]["r"])
                
                dx, dy = msg["dx"], msg["dy"]
                dist = (dx**2 + dy**2)**0.5
                if dist > 0:
                    dx = dx / dist * player_speed
                    dy = dy / dist * player_speed
                
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
                    if other_name != name and not other.get("dead", False):
                        dist = ((players[name]["x"] - other["x"])**2 + (players[name]["y"] - other["y"])**2)**0.5
                        if dist < players[name]["r"] and players[name]["r"] > other["r"] + 5:
                            eater_name = name
                            eaten_name = other_name
                            
                            if other_name in bots:
                                bots[eaten_name]["dead"] = True
                                if eaten_name not in bot_respawn_tasks:
                                    bot_respawn_tasks[eaten_name] = asyncio.create_task(respawn_bot(eaten_name))
                            elif other_name in players:
                                players[eaten_name]["dead"] = True
                                try:
                                    await connections[eaten_name].send_json({
                                        "type": "death",
                                        "killer": eater_name
                                    })
                                except:
                                    pass
                            
                            players[name]["r"] += int(other["r"] * 0.6)
                            
                            for ws_name, ws_conn in list(connections.items()):
                                try:
                                    await ws_conn.send_json({
                                        "type": "eat",
                                        "eater": eater_name,
                                        "eaten": eaten_name
                                    })
                                except:
                                    pass

            elif msg["type"] == "chat" and not players[name]["dead"]:
                message = msg.get("message", "").strip()[:CHAT_MESSAGE_LENGTH]
                if message:
                    chat_messages.append({
                        "sender": name,
                        "message": message,
                        "color": players[name]["color"]
                    })
                    if len(chat_messages) > MAX_CHAT_MESSAGES:
                        chat_messages.pop(0)

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
        # Отправляем уведомление в чат о выходе игрока
        chat_messages.append({
            "sender": "Система",
            "message": f"{name} покинул игру.",
            "color": [100, 100, 100]
        })
        if len(chat_messages) > MAX_CHAT_MESSAGES:
            chat_messages.pop(0)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
