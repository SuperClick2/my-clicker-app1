from flask import Flask, render_template, request
from threading import Thread
import asyncio
import random
import uuid
from typing import Dict, List
from datetime import datetime
import hashlib
import secrets
import websockets
import json

app = Flask(__name__)

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
BOT_SPEED = 5
BOT_RESPAWN_TIME = 9
MAX_NAME_LENGTH = 15
MAX_CONNECTIONS = 100
CONNECTION_RATE_LIMIT = 5

# Состояние игры
players: Dict[str, dict] = {}
foods: List[dict] = []
portals: List[dict] = []
connections: Dict[str, websockets.WebSocketServerProtocol] = {}
bots: Dict[str, dict] = {}
bot_respawn_tasks: Dict[str, asyncio.Task] = {}
connection_times: List[datetime] = []

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
                    eater_name = bot_name
                    eaten_name = closest_target["name"]
                    
                    if eaten_name in bots:
                        bots[eaten_name]["dead"] = True
                        if eaten_name not in bot_respawn_tasks:
                            bot_respawn_tasks[eaten_name] = asyncio.create_task(respawn_bot(eaten_name))
                    elif eaten_name in players:
                        players[eaten_name]["dead"] = True
                        try:
                            await connections[eaten_name].send(json.dumps({
                                "type": "death",
                                "killer": eater_name
                            }))
                        except:
                            pass
                    
                    bot["r"] += int(closest_target["r"] * 0.6)
                    
                    for ws_name, ws_conn in list(connections.items()):
                        try:
                            await ws_conn.send(json.dumps({
                                "type": "eat",
                                "eater": eater_name,
                                "eaten": eaten_name
                            }))
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
                    await connections[name].send(json.dumps({
                        "type": "error",
                        "message": "Ошибка: ваш размер превысил максимально допустимый!"
                    }))
                    await disconnect(name)
                except:
                    pass

        all_players = {k: v for k, v in {**players, **bots}.items() if not v.get("dead", False)}
        for name, ws in list(connections.items()):
            try:
                await ws.send(json.dumps({
                    "type": "update",
                    "players": all_players,
                    "foods": foods,
                    "portals": portals
                }))
            except:
                await disconnect(name)

        await asyncio.sleep(0.05)

async def disconnect(name: str):
    if name in connections:
        del connections[name]
    if name in players:
        del players[name]

async def websocket_handler(websocket, path):
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
        data = json.loads(await websocket.recv())
        if data["type"] != "join" or not data.get("name"):
            await websocket.send(json.dumps({"type": "error", "message": "Неверный запрос на подключение"}))
            await websocket.close()
            return
        
        name = data["name"]
        
        if not validate_name(name):
            await websocket.send(json.dumps({
                "type": "error",
                "message": f"Недопустимое имя. Используйте только буквы, цифры и _-. Максимум {MAX_NAME_LENGTH} символов."
            }))
            await websocket.close()
            return
        
        if name in players or name in bots:
            await websocket.send(json.dumps({
                "type": "error",
                "message": "Это имя уже занято. Пожалуйста, выберите другое."
            }))
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

        while True:
            msg = json.loads(await websocket.recv())
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
                                    await connections[eaten_name].send(json.dumps({
                                        "type": "death",
                                        "killer": eater_name
                                    }))
                                except:
                                    pass
                            
                            players[name]["r"] += int(other["r"] * 0.6)
                            
                            for ws_name, ws_conn in list(connections.items()):
                                try:
                                    await ws_conn.send(json.dumps({
                                        "type": "eat",
                                        "eater": eater_name,
                                        "eaten": eaten_name
                                    }))
                                except:
                                    pass

    except websockets.exceptions.ConnectionClosed:
        if name:
            await disconnect(name)
    except Exception as e:
        print("Ошибка в WebSocket:", e)
        if name:
            await disconnect(name)

async def start_server():
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
    
    server = await websockets.serve(
        websocket_handler,
        "0.0.0.0",
        8000
    )
    
    await server.wait_closed()
    game_task.cancel()
    bot_task.cancel()
    for task in bot_respawn_tasks.values():
        task.cancel()
    try:
        await game_task
        await bot_task
    except asyncio.CancelledError:
        pass

def run_server():
    asyncio.run(start_server())

@app.route('/')
def index():
    return render_template('index.html')

# HTML шаблон
@app.route('/template')
def template():
    return """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Agar.io Mobile</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            touch-action: none;
        }
        
        body {
            font-family: Arial, sans-serif;
            overflow: hidden;
            background-color: #f0f0f0;
            position: fixed;
            width: 100%;
            height: 100%;
        }
        
        #gameCanvas {
            display: block;
            background-color: white;
            position: absolute;
            top: 0;
            left: 0;
        }
        
        #menuScreen {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(255, 255, 255, 0.9);
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            z-index: 10;
        }
        
        #gameTitle {
            font-size: 2.5rem;
            margin-bottom: 2rem;
            color: #333;
            text-align: center;
        }
        
        .input-group {
            margin-bottom: 1.5rem;
            width: 80%;
            max-width: 300px;
        }
        
        label {
            display: block;
            margin-bottom: 0.5rem;
            font-size: 1.1rem;
            color: #333;
        }
        
        input {
            width: 100%;
            padding: 0.8rem;
            font-size: 1rem;
            border: 2px solid #ccc;
            border-radius: 5px;
        }
        
        #colorPreview {
            width: 60px;
            height: 60px;
            border: 2px solid #333;
            border-radius: 5px;
            margin: 0 auto 1.5rem;
            cursor: pointer;
        }
        
        .btn {
            background-color: #4CAF50;
            color: white;
            border: none;
            padding: 1rem 2rem;
            font-size: 1.2rem;
            border-radius: 5px;
            cursor: pointer;
            margin: 0.5rem;
            width: 80%;
            max-width: 300px;
            text-align: center;
            transition: background-color 0.3s;
        }
        
        .btn:hover {
            background-color: #45a049;
        }
        
        #deathScreen {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(255, 255, 255, 0.9);
            display: none;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            z-index: 20;
        }
        
        #deathMessage {
            font-size: 1.5rem;
            margin-bottom: 2rem;
            color: #d32f2f;
            text-align: center;
        }
        
        #leaderboard {
            position: absolute;
            top: 10px;
            right: 10px;
            background-color: rgba(255, 255, 255, 0.7);
            padding: 10px;
            border-radius: 5px;
            max-width: 150px;
            z-index: 5;
        }
        
        #leaderboard h3 {
            margin-bottom: 5px;
            font-size: 1rem;
        }
        
        #leaderboardList {
            list-style-type: none;
        }
        
        #leaderboardList li {
            margin-bottom: 3px;
            font-size: 0.9rem;
        }
        
        #controls {
            position: absolute;
            bottom: 20px;
            left: 0;
            width: 100%;
            display: flex;
            justify-content: center;
            z-index: 5;
        }
        
        .control-btn {
            width: 60px;
            height: 60px;
            background-color: rgba(0, 0, 0, 0.3);
            border-radius: 50%;
            margin: 0 10px;
            display: flex;
            justify-content: center;
            align-items: center;
            color: white;
            font-size: 1.5rem;
            user-select: none;
        }
        
        #joystick {
            position: absolute;
            bottom: 30px;
            left: 30px;
            width: 100px;
            height: 100px;
            background-color: rgba(0, 0, 0, 0.2);
            border-radius: 50%;
            display: none;
            z-index: 5;
        }
        
        #joystickKnob {
            position: absolute;
            width: 40px;
            height: 40px;
            background-color: rgba(0, 0, 0, 0.5);
            border-radius: 50%;
            top: 30px;
            left: 30px;
        }
        
        #eatMessages {
            position: absolute;
            top: 10px;
            left: 10px;
            background-color: rgba(255, 255, 255, 0.7);
            padding: 10px;
            border-radius: 5px;
            max-width: 200px;
            z-index: 5;
        }
        
        #eatMessages h3 {
            margin-bottom: 5px;
            font-size: 1rem;
        }
        
        #eatMessagesList {
            list-style-type: none;
        }
        
        #eatMessagesList li {
            margin-bottom: 3px;
            font-size: 0.9rem;
            color: #d32f2f;
        }
        
        @media (max-width: 600px) {
            #gameTitle {
                font-size: 2rem;
            }
            
            .btn {
                padding: 0.8rem 1.5rem;
                font-size: 1rem;
            }
            
            #leaderboard {
                max-width: 120px;
                padding: 5px;
            }
            
            #leaderboard h3 {
                font-size: 0.8rem;
            }
            
            #leaderboardList li {
                font-size: 0.7rem;
            }
            
            .control-btn {
                width: 50px;
                height: 50px;
                font-size: 1.2rem;
            }
        }
    </style>
</head>
<body>
    <canvas id="gameCanvas"></canvas>
    
    <div id="leaderboard">
        <h3>Лидеры</h3>
        <ul id="leaderboardList"></ul>
    </div>
    
    <div id="eatMessages">
        <h3>События</h3>
        <ul id="eatMessagesList"></ul>
    </div>
    
    <div id="controls">
        <div class="control-btn" id="btnUp">↑</div>
        <div class="control-btn" id="btnLeft">←</div>
        <div class="control-btn" id="btnDown">↓</div>
        <div class="control-btn" id="btnRight">→</div>
    </div>
    
    <div id="joystick">
        <div id="joystickKnob"></div>
    </div>
    
    <div id="menuScreen">
        <h1 id="gameTitle">Agar.io Mobile</h1>
        
        <div class="input-group">
            <label for="playerName">Имя игрока:</label>
            <input type="text" id="playerName" maxlength="15" placeholder="Введите имя" value="Player">
        </div>
        
        <div class="input-group">
            <label>Цвет игрока:</label>
            <div id="colorPreview"></div>
        </div>
        
        <button class="btn" id="changeColorBtn">Сменить цвет</button>
        <button class="btn" id="playBtn">Играть</button>
    </div>
    
    <div id="deathScreen">
        <h1 id="deathMessage"></h1>
        <button class="btn" id="backToMenuBtn">В меню</button>
    </div>
    
    <script>
        // Конфигурация игры
        const MAP_WIDTH = 3000;
        const MAP_HEIGHT = 3000;
        const SERVER_URL = "wss://" + window.location.host + "/ws";
        
        // Состояние игры
        let gameState = {
            player: {
                x: 0,
                y: 0,
                r: 10,
                name: "Player",
                color: [255, 0, 0],
                dead: false
            },
            players: {},
            foods: [],
            portals: [],
            leaderboard: [],
            eatMessages: [],
            lastEatMessages: [],
            error: "",
            killer: "",
            connected: false,
            ws: null,
            camera: {
                x: 0,
                y: 0
            },
            controls: {
                up: false,
                down: false,
                left: false,
                right: false
            },
            joystick: {
                active: false,
                startX: 0,
                startY: 0,
                moveX: 0,
                moveY: 0
            }
        };
        
        // Элементы DOM
        const canvas = document.getElementById('gameCanvas');
        const ctx = canvas.getContext('2d');
        const menuScreen = document.getElementById('menuScreen');
        const deathScreen = document.getElementById('deathScreen');
        const deathMessage = document.getElementById('deathMessage');
        const playerNameInput = document.getElementById('playerName');
        const colorPreview = document.getElementById('colorPreview');
        const changeColorBtn = document.getElementById('changeColorBtn');
        const playBtn = document.getElementById('playBtn');
        const backToMenuBtn = document.getElementById('backToMenuBtn');
        const leaderboardList = document.getElementById('leaderboardList');
        const eatMessagesList = document.getElementById('eatMessagesList');
        const btnUp = document.getElementById('btnUp');
        const btnDown = document.getElementById('btnDown');
        const btnLeft = document.getElementById('btnLeft');
        const btnRight = document.getElementById('btnRight');
        const joystick = document.getElementById('joystick');
        const joystickKnob = document.getElementById('joystickKnob');
        
        // Настройка canvas
        function resizeCanvas() {
            canvas.width = window.innerWidth;
            canvas.height = window.innerHeight;
        }
        
        resizeCanvas();
        window.addEventListener('resize', resizeCanvas);
        
        // Генерация случайного цвета
        function generateRandomColor() {
            const hue = Math.floor(Math.random() * 360);
            const saturation = 80 + Math.floor(Math.random() * 20);
            const lightness = 50 + Math.floor(Math.random() * 30);
            
            return hslToRgb(hue / 360, saturation / 100, lightness / 100);
        }
        
        // Конвертация HSL в RGB
        function hslToRgb(h, s, l) {
            let r, g, b;
            
            if (s === 0) {
                r = g = b = l;
            } else {
                const hue2rgb = (p, q, t) => {
                    if (t < 0) t += 1;
                    if (t > 1) t -= 1;
                    if (t < 1/6) return p + (q - p) * 6 * t;
                    if (t < 1/2) return q;
                    if (t < 2/3) return p + (q - p) * (2/3 - t) * 6;
                    return p;
                };
                
                const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
                const p = 2 * l - q;
                
                r = hue2rgb(p, q, h + 1/3);
                g = hue2rgb(p, q, h);
                b = hue2rgb(p, q, h - 1/3);
            }
            
            return [
                Math.round(r * 255),
                Math.round(g * 255),
                Math.round(b * 255)
            ];
        }
        
        // Обновление предпросмотра цвета
        function updateColorPreview(color) {
            colorPreview.style.backgroundColor = `rgb(${color[0]}, ${color[1]}, ${color[2]})`;
        }
        
        // Инициализация цвета
        gameState.player.color = generateRandomColor();
        updateColorPreview(gameState.player.color);
        
        // Смена цвета
        changeColorBtn.addEventListener('click', () => {
            gameState.player.color = generateRandomColor();
            updateColorPreview(gameState.player.color);
        });
        
        // Подключение к серверу
        function connectToServer(name, color) {
            if (gameState.ws) {
                gameState.ws.close();
            }
            
            gameState.ws = new WebSocket(SERVER_URL);
            gameState.connected = false;
            
            gameState.ws.onopen = () => {
                gameState.ws.send(JSON.stringify({
                    type: "join",
                    name: name,
                    color: color
                }));
            };
            
            gameState.ws.onmessage = (event) => {
                const msg = JSON.parse(event.data);
                
                switch (msg.type) {
                    case "update":
                        gameState.players = msg.players;
                        gameState.foods = msg.foods;
                        gameState.portals = msg.portals || [];
                        
                        if (name in gameState.players) {
                            gameState.player = gameState.players[name];
                            gameState.camera.x = gameState.player.x - canvas.width / 2;
                            gameState.camera.y = gameState.player.y - canvas.height / 2;
                            
                            // Обновление лидерборда
                            gameState.leaderboard = Object.values(gameState.players)
                                .concat(Object.values(gameState.bots || {}))
                                .filter(p => !p.dead)
                                .sort((a, b) => b.r - a.r)
                                .slice(0, 10);
                        }
                        break;
                    
                    case "death":
                        gameState.player.dead = true;
                        gameState.killer = msg.killer;
                        showDeathScreen(`Ты умер. Тебя съел ${msg.killer}`);
                        break;
                    
                    case "eat":
                        gameState.eatMessages.push({
                            text: `${msg.eater} съел ${msg.eaten}`,
                            timestamp: Date.now()
                        });
                        updateEatMessages();
                        break;
                    
                    case "error":
                        gameState.error = msg.message;
                        showDeathScreen(msg.message);
                        break;
                }
            };
            
            gameState.ws.onclose = () => {
                if (!gameState.player.dead && !gameState.error) {
                    showDeathScreen("Соединение с сервером потеряно");
                }
                gameState.connected = false;
            };
            
            gameState.ws.onerror = (error) => {
                console.error("WebSocket error:", error);
                showDeathScreen("Ошибка соединения с сервером");
                gameState.connected = false;
            };
        }
        
        // Обновление сообщений о съедении
        function updateEatMessages() {
            const now = Date.now();
            gameState.eatMessages = gameState.eatMessages.filter(msg => now - msg.timestamp < 5000);
            
            eatMessagesList.innerHTML = '';
            gameState.eatMessages.slice(-5).forEach(msg => {
                const li = document.createElement('li');
                li.textContent = msg.text;
                eatMessagesList.appendChild(li);
            });
        }
        
        // Обновление лидерборда
        function updateLeaderboard() {
            leaderboardList.innerHTML = '';
            gameState.leaderboard.forEach((player, index) => {
                const li = document.createElement('li');
                li.textContent = `${index + 1}. ${player.name} (${Math.round(player.r)})`;
                
                if (index === 0) {
                    li.style.color = 'gold';
                    li.style.fontWeight = 'bold';
                } else if (player.name === gameState.player.name) {
                    li.style.color = `rgb(${gameState.player.color.join(',')})`;
                    li.style.fontWeight = 'bold';
                }
                
                leaderboardList.appendChild(li);
            });
        }
        
        // Показать экран смерти
        function showDeathScreen(message) {
            deathMessage.textContent = message;
            deathScreen.style.display = 'flex';
        }
        
        // Отрисовка игры
        function drawGame() {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            
            // Рисуем фон
            ctx.fillStyle = 'white';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            
            // Если игрок мертв или не подключен, не рисуем игру
            if (gameState.player.dead || !gameState.connected) return;
            
            // Центрируем камеру на игроке
            gameState.camera.x = gameState.player.x - canvas.width / 2;
            gameState.camera.y = gameState.player.y - canvas.height / 2;
            
            // Рисуем границы карты
            ctx.strokeStyle = 'black';
            ctx.lineWidth = 5;
            ctx.strokeRect(
                -gameState.camera.x,
                -gameState.camera.y,
                MAP_WIDTH,
                MAP_HEIGHT
            );
            
            // Рисуем еду
            ctx.fillStyle = 'green';
            gameState.foods.forEach(food => {
                ctx.beginPath();
                ctx.arc(
                    food.x - gameState.camera.x,
                    food.y - gameState.camera.y,
                    5,
                    0,
                    Math.PI * 2
                );
                ctx.fill();
            });
            
            // Рисуем порталы
            gameState.portals.forEach(portal => {
                const x = portal.x - gameState.camera.x;
                const y = portal.y - gameState.camera.y;
                
                // Внешний круг
                ctx.fillStyle = portal.type === 'mass' ? 'blue' : 'purple';
                ctx.beginPath();
                ctx.arc(x, y, portal.r, 0, Math.PI * 2);
                ctx.fill();
                
                // Внутренний круг
                ctx.fillStyle = portal.type === 'mass' ? 'lightblue' : 'plum';
                ctx.beginPath();
                ctx.arc(x, y, portal.r - 5, 0, Math.PI * 2);
                ctx.fill();
            });
            
            // Рисуем игроков и ботов
            const allPlayers = Object.values(gameState.players).concat(Object.values(gameState.bots || {}));
            allPlayers.forEach(player => {
                if (player.dead) return;
                
                const x = player.x - gameState.camera.x;
                const y = player.y - gameState.camera.y;
                const radius = player.r;
                const color = player.color ? `rgb(${player.color.join(',')})` : 'blue';
                
                // Рисуем игрока
                ctx.fillStyle = color;
                ctx.beginPath();
                ctx.arc(x, y, radius, 0, Math.PI * 2);
                ctx.fill();
                
                // Рисуем имя
                ctx.fillStyle = 'black';
                ctx.font = `${Math.max(12, Math.min(20, radius / 3))}px Arial`;
                ctx.textAlign = 'center';
                ctx.fillText(player.name, x, y - radius - 10);
                
                // Подсветка лидера
                if (gameState.leaderboard.length > 0 && player === gameState.leaderboard[0]) {
                    ctx.strokeStyle = 'gold';
                    ctx.lineWidth = 3;
                    ctx.beginPath();
                    ctx.arc(x, y, radius + 5, 0, Math.PI * 2);
                    ctx.stroke();
                }
            });
            
            // Обновляем лидерборд
            updateLeaderboard();
        }
        
        // Управление с кнопок
        function setupButtonControls() {
            const setControl = (control, value) => {
                gameState.controls[control] = value;
                sendMoveCommand();
            };
            
            btnUp.addEventListener('touchstart', () => setControl('up', true));
            btnUp.addEventListener('touchend', () => setControl('up', false));
            btnUp.addEventListener('mousedown', () => setControl('up', true));
            btnUp.addEventListener('mouseup', () => setControl('up', false));
            btnUp.addEventListener('mouseleave', () => setControl('up', false));
            
            btnDown.addEventListener('touchstart', () => setControl('down', true));
            btnDown.addEventListener('touchend', () => setControl('down', false));
            btnDown.addEventListener('mousedown', () => setControl('down', true));
            btnDown.addEventListener('mouseup', () => setControl('down', false));
            btnDown.addEventListener('mouseleave', () => setControl('down', false));
            
            btnLeft.addEventListener('touchstart', () => setControl('left', true));
            btnLeft.addEventListener('touchend', () => setControl('left', false));
            btnLeft.addEventListener('mousedown', () => setControl('left', true));
            btnLeft.addEventListener('mouseup', () => setControl('left', false));
            btnLeft.addEventListener('mouseleave', () => setControl('left', false));
            
            btnRight.addEventListener('touchstart', () => setControl('right', true));
            btnRight.addEventListener('touchend', () => setControl('right', false));
            btnRight.addEventListener('mousedown', () => setControl('right', true));
            btnRight.addEventListener('mouseup', () => setControl('right', false));
            btnRight.addEventListener('mouseleave', () => setControl('right', false));
        }
        
        // Управление джойстиком
        function setupJoystick() {
            const joystickRadius = joystick.offsetWidth / 2;
            const knobRadius = joystickKnob.offsetWidth / 2;
            
            const startJoystick = (e) => {
                e.preventDefault();
                const rect = joystick.getBoundingClientRect();
                gameState.joystick.active = true;
                gameState.joystick.startX = rect.left + joystickRadius;
                gameState.joystick.startY = rect.top + joystickRadius;
                joystick.style.display = 'block';
                joystick.style.left = `${e.touches ? e.touches[0].clientX - joystickRadius : e.clientX - joystickRadius}px`;
                joystick.style.top = `${e.touches ? e.touches[0].clientY - joystickRadius : e.clientY - joystickRadius}px`;
                moveJoystick(e);
            };
            
            const moveJoystick = (e) => {
                if (!gameState.joystick.active) return;
                e.preventDefault();
                
                const clientX = e.touches ? e.touches[0].clientX : e.clientX;
                const clientY = e.touches ? e.touches[0].clientY : e.clientY;
                
                const dx = clientX - gameState.joystick.startX;
                const dy = clientY - gameState.joystick.startY;
                const distance = Math.sqrt(dx * dx + dy * dy);
                
                if (distance < joystickRadius) {
                    gameState.joystick.moveX = dx;
                    gameState.joystick.moveY = dy;
                } else {
                    gameState.joystick.moveX = dx / distance * joystickRadius;
                    gameState.joystick.moveY = dy / distance * joystickRadius;
                }
                
                joystickKnob.style.left = `${joystickRadius + gameState.joystick.moveX - knobRadius}px`;
                joystickKnob.style.top = `${joystickRadius + gameState.joystick.moveY - knobRadius}px`;
                
                sendMoveCommand();
            };
            
            const endJoystick = () => {
                gameState.joystick.active = false;
                gameState.joystick.moveX = 0;
                gameState.joystick.moveY = 0;
                joystick.style.display = 'none';
                sendMoveCommand();
            };
            
            // Сенсорные события
            joystick.addEventListener('touchstart', startJoystick);
            document.addEventListener('touchmove', moveJoystick);
            document.addEventListener('touchend', endJoystick);
            
            // Мышиные события
            joystick.addEventListener('mousedown', startJoystick);
            document.addEventListener('mousemove', moveJoystick);
            document.addEventListener('mouseup', endJoystick);
        }
        
        // Отправка команды движения
        function sendMoveCommand() {
            if (!gameState.connected || gameState.player.dead) return;
            
            let dx = 0, dy = 0;
            const speed = 5;
            
            if (gameState.joystick.active) {
                const joystickRadius = joystick.offsetWidth / 2;
                dx = gameState.joystick.moveX / joystickRadius * speed;
                dy = gameState.joystick.moveY / joystickRadius * speed;
            } else {
                if (gameState.controls.up) dy -= speed;
                if (gameState.controls.down) dy += speed;
                if (gameState.controls.left) dx -= speed;
                if (gameState.controls.right) dx += speed;
            }
            
            if (dx !== 0 || dy !== 0) {
                gameState.ws.send(JSON.stringify({
                    type: "move",
                    dx: dx,
                    dy: dy
                }));
            }
        }
        
        // Главное меню
        function setupMainMenu() {
            playBtn.addEventListener('click', () => {
                const name = playerNameInput.value.trim() || "Player";
                menuScreen.style.display = 'none';
                connectToServer(name, gameState.player.color);
                gameLoop();
            });
            
            backToMenuBtn.addEventListener('click', () => {
                if (gameState.ws) {
                    gameState.ws.close();
                }
                deathScreen.style.display = 'none';
                menuScreen.style.display = 'flex';
            });
        }
        
        // Игровой цикл
        function gameLoop() {
            drawGame();
            requestAnimationFrame(gameLoop);
        }
        
        // Инициализация
        function init() {
            setupMainMenu();
            setupButtonControls();
            setupJoystick();
            gameLoop();
        }
        
        // Запуск приложения
        init();
    </script>
</body>
</html>
    """

if __name__ == '__main__':
    # Запускаем сервер в отдельном потоке
    server_thread = Thread(target=run_server)
    server_thread.daemon = True
    server_thread.start()
    
    # Запускаем Flask приложение
    app.run(host='0.0.0.0', port=5000)
