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
import math

# Конфигурация игры
MAP_WIDTH, MAP_HEIGHT = 3000, 3000
MAX_FOOD = 200
MAX_PORTALS = 15
MIN_PORTALS = 10
PORTAL_RADIUS = 20
MASS_LOSS_THRESHOLD = 150
MASS_LOSS_INTERVAL = 1
MIN_MASS_LOSS = 2
MAX_MASS_LOSS = 9
MASS_PORTAL_BONUS = 40
MAX_PLAYER_MASS_FOR_PORTAL = 150
MAX_PLAYER_MASS = 1000
BOT_NAMES = ["Bot_Alpha", "Bot_Beta", "Bot_Gamma", "Bot_Delta", "Bot_Epsilon",
             "Bot_Zeta", "Bot_Eta", "Bot_Theta", "Bot_Iota", "Bot_Kappa"]
BOT_COUNT = 10
BOT_UPDATE_INTERVAL = 0.1
BASE_SPEED = 5
BOT_SPEED = BASE_SPEED
BOT_RESPAWN_TIME = 9
MAX_NAME_LENGTH = 15
MAX_CONNECTIONS = 100
CONNECTION_RATE_LIMIT = 5
BOT_STRATEGY_CHANGE_INTERVAL = 10  # Как часто боты меняют стратегию

# Состояние игры
players: Dict[str, dict] = {}
foods: List[dict] = []
portals: List[dict] = []
connections: Dict[str, WebSocket] = {}
bots: Dict[str, dict] = {}
bot_respawn_tasks: Dict[str, asyncio.Task] = {}
connection_times: List[datetime] = []
bot_strategies: Dict[str, dict] = {}  # Стратегии поведения ботов

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
    # Чем больше масса, тем медленнее движение (но не менее 1)
    return max(1, BASE_SPEED * (50 / max(10, mass)))

def distance(x1, y1, x2, y2):
    return math.sqrt((x1 - x2)**2 + (y1 - y2)**2)

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

def get_bot_strategy(bot_name):
    # Определяем стратегию поведения бота
    if bot_name not in bot_strategies or random.random() < 0.05:
        # Выбираем случайную стратегию или меняем существующую с небольшой вероятностью
        strategy = random.choice(["aggressive", "defensive", "balanced", "scared", "farmer"])
        target_types = random.choices(
            ["player", "bot", "food", "portal"],
            weights=[0.4, 0.3, 0.2, 0.1],
            k=2
        )
        bot_strategies[bot_name] = {
            "type": strategy,
            "preferred_targets": target_types,
            "last_change": datetime.now()
        }
    return bot_strategies[bot_name]

async def bot_behavior():
    while True:
        for bot_name, bot in list(bots.items()):
            if bot["dead"]:
                continue
                
            strategy = get_bot_strategy(bot_name)
            current_time = datetime.now()
            
            # Меняем стратегию, если прошло много времени
            if (current_time - strategy["last_change"]).total_seconds() > BOT_STRATEGY_CHANGE_INTERVAL:
                strategy = get_bot_strategy(bot_name)  # Обновляем стратегию
            
            # Поиск целей в зависимости от стратегии
            closest_target = None
            min_dist = float('inf')
            is_food = True
            target_value = 0
            
            # Анализируем все возможные цели
            for target_type in ["players", "bots", "food", "portals"]:
                if target_type == "players":
                    targets = players.values()
                elif target_type == "bots":
                    targets = bots.values()
                elif target_type == "food":
                    targets = foods
                elif target_type == "portals":
                    targets = portals
                
                for target in targets:
                    if target_type in ["players", "bots"]:
                        if target["name"] == bot_name or target.get("dead", False):
                            continue
                            
                        dist = distance(bot["x"], bot["y"], target["x"], target["y"])
                        value = 0
                        
                        # Оцениваем цель в зависимости от стратегии
                        if strategy["type"] == "aggressive":
                            if target["r"] < bot["r"] * 0.8:
                                value = (bot["r"] - target["r"]) / dist * 10
                            elif target["r"] > bot["r"] * 1.2:
                                value = -10 / dist
                        elif strategy["type"] == "defensive":
                            if target["r"] < bot["r"] * 0.7:
                                value = (bot["r"] - target["r"]) / dist * 5
                            elif target["r"] > bot["r"] * 1.1:
                                value = -15 / dist
                        elif strategy["type"] == "scared":
                            if target["r"] < bot["r"] * 0.6:
                                value = (bot["r"] - target["r"]) / dist * 3
                            elif target["r"] > bot["r"] * 0.9:
                                value = -20 / dist
                        elif strategy["type"] == "farmer":
                            value = -5 / dist  # Предпочитает избегать всех
                        else:  # balanced
                            if target["r"] < bot["r"] * 0.75:
                                value = (bot["r"] - target["r"]) / dist * 7
                            elif target["r"] > bot["r"] * 1.15:
                                value = -10 / dist
                        
                        if value > target_value or (value == target_value and dist < min_dist):
                            target_value = value
                            min_dist = dist
                            closest_target = target
                            is_food = False
                    
                    elif target_type == "food" and "food" in strategy["preferred_targets"]:
                        dist = distance(bot["x"], bot["y"], target["x"], target["y"])
                        value = 1 / dist * (3 if strategy["type"] == "farmer" else 1)
                        
                        if value > target_value or (value == target_value and dist < min_dist):
                            target_value = value
                            min_dist = dist
                            closest_target = target
                            is_food = True
                    
                    elif target_type == "portals" and "portal" in strategy["preferred_targets"]:
                        dist = distance(bot["x"], bot["y"], target["x"], target["y"])
                        if target["type"] == "mass" and bot["r"] < MAX_PLAYER_MASS_FOR_PORTAL:
                            value = 2 / dist
                        else:
                            value = 0.5 / dist
                        
                        if value > target_value or (value == target_value and dist < min_dist):
                            target_value = value
                            min_dist = dist
                            closest_target = target
                            is_food = False
            
            # Движение к цели с учетом массы
            if closest_target:
                dx, dy = 0, 0
                if is_food:
                    dx = closest_target["x"] - bot["x"]
                    dy = closest_target["y"] - bot["y"]
                else:
                    if target_value > 0:  # Атакуем
                        dx = closest_target["x"] - bot["x"]
                        dy = closest_target["y"] - bot["y"]
                    else:  # Убегаем
                        dx = bot["x"] - closest_target["x"]
                        dy = bot["y"] - closest_target["y"]
                
                # Нормализация вектора и учет массы
                dist = math.sqrt(dx**2 + dy**2)
                if dist > 0:
                    speed = calculate_speed(bot["r"])
                    dx = dx / dist * speed
                    dy = dy / dist * speed
                
                bot["x"] += dx
                bot["y"] += dy
                
                # Ограничение движения
                bot["x"] = max(0, min(MAP_WIDTH, bot["x"]))
                bot["y"] = max(0, min(MAP_HEIGHT, bot["y"]))
                
                # Взаимодействие с целями
                if is_food and min_dist < bot["r"]:
                    foods.remove(closest_target)
                    bot["r"] += 1
                elif not is_food and min_dist < bot["r"] and closest_target["r"] < bot["r"] - 5:
                    # Удаление съеденного объекта
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
                    
                    # Увеличение массы бота
                    bot["r"] += int(closest_target["r"] * 0.6)
                    
                    # Отправка сообщения о съедении
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
        # Генерация еды
        while len(foods) < MAX_FOOD:
            foods.append(generate_food())

        # Генерация порталов
        current_time = datetime.now()
        if (current_time - last_portal_spawn).total_seconds() > 10 and len(portals) < MAX_PORTALS:
            portals.append(generate_portal())
            last_portal_spawn = current_time

        # Потеря массы для больших игроков и ботов
        for entity in list(players.values()) + list(bots.values()):
            if not entity.get("dead", False) and entity["r"] >= MASS_LOSS_THRESHOLD:
                now = datetime.now().timestamp()
                if "mass_loss_timer" not in entity or now - entity["mass_loss_timer"] >= MASS_LOSS_INTERVAL:
                    mass_loss = calculate_mass_loss(entity["r"])
                    entity["r"] = max(10, entity["r"] - mass_loss)
                    entity["mass_loss_timer"] = now

        # Взаимодействие с порталами
        for name, player in list(players.items()):
            if player["dead"]:
                continue
                
            interacted_portals = []
            for portal in portals:
                dist = distance(player["x"], player["y"], portal["x"], portal["y"])
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

            # Удаление использованных порталов
            for portal in interacted_portals:
                if portal in portals:
                    portals.remove(portal)

        # Проверка на слишком большой размер игрока
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

        # Отправка обновлений всем игрокам
        all_players = {k: v for k, v in {**players, **bots}.items() if not v.get("dead", False)}
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
    # Создание ботов
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
        # Инициализация стратегии для бота
        get_bot_strategy(bot_name)
    
    # Запуск игровых циклов
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

# Настройка middleware для безопасности
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
    # Проверка rate limiting
    if not check_rate_limit():
        await websocket.close()
        return
    
    connection_times.append(datetime.now())
    
    # Проверка максимального количества подключений
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
        
        # Валидация имени
        if not validate_name(name):
            await websocket.send_json({
                "type": "error",
                "message": f"Недопустимое имя. Используйте только буквы, цифры и _-. Максимум {MAX_NAME_LENGTH} символов."
            })
            await websocket.close()
            return
        
        # Проверка на существующее имя
        if name in players or name in bots:
            await websocket.send_json({
                "type": "error",
                "message": "Это имя уже занято. Пожалуйста, выберите другое."
            })
            await websocket.close()
            return
        
        # Валидация цвета
        color = data.get("color", [255, 0, 0])
        if not validate_color(color):
            color = [255, 0, 0]
        
        # Создание игрока
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

        # Игровой цикл для конкретного игрока
        while True:
            msg = await websocket.receive_json()
            if msg["type"] == "move" and not players[name]["dead"]:
                dx, dy = msg["dx"], msg["dy"]
                
                # Нормализация вектора и учет массы
                dist = math.sqrt(dx**2 + dy**2)
                if dist > 0:
                    speed = calculate_speed(players[name]["r"])
                    dx = dx / dist * speed
                    dy = dy / dist * speed
                
                players[name]["x"] += dx
                players[name]["y"] += dy
                
                # Ограничение движения
                players[name]["x"] = max(0, min(MAP_WIDTH, players[name]["x"]))
                players[name]["y"] = max(0, min(MAP_HEIGHT, players[name]["y"]))

                # Съедание еды
                eaten = []
                for food in foods:
                    dist = distance(players[name]["x"], players[name]["y"], food["x"], food["y"])
                    if dist < players[name]["r"]:
                        players[name]["r"] += 1
                        eaten.append(food)
                for food in eaten:
                    foods.remove(food)

                # Съедание других игроков/ботов
                for other_name, other in list({**players, **bots}.items()):
                    if other_name != name and not other.get("dead", False):
                        dist = distance(players[name]["x"], players[name]["y"], other["x"], other["y"])
                        if dist < players[name]["r"] and players[name]["r"] > other["r"] + 5:
                            # Удаление съеденного объекта
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
                            
                            # Увеличение массы игрока
                            players[name]["r"] += int(other["r"] * 0.6)
                            
                            # Отправка сообщения о съедении
                            for ws_name, ws_conn in list(connections.items()):
                                try:
                                    await ws_conn.send_json({
                                        "type": "eat",
                                        "eater": eater_name,
                                        "eaten": eaten_name
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
