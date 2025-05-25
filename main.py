# app.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.wsgi import WSGIMiddleware
from flask import Flask, render_template_string
import asyncio
import uvicorn
import random
import uuid

# === HTML-КЛИЕНТ ===
html_page = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>Agar Multiplayer</title>
  <style>
    html, body { margin: 0; padding: 0; overflow: hidden; }
    canvas { background: #fff; display: block; width: 100vw; height: 100vh; }
  </style>
</head>
<body>
<canvas id="game"></canvas>
<script>
  const canvas = document.getElementById('game');
  const ctx = canvas.getContext('2d');
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;

  const name = prompt("Ваше имя:", "Player") || "Player";
  const color = [Math.floor(Math.random()*255), Math.floor(Math.random()*255), Math.floor(Math.random()*255)];

  const ws = new WebSocket(`ws://${location.host}/ws`);
  let player = { x: 0, y: 0, r: 10 };

  ws.onopen = () => {
    ws.send(JSON.stringify({ type: "join", name: name, color: color }));
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "update") {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const players = msg.players;

      for (const p of Object.values(players)) {
        ctx.beginPath();
        ctx.arc(p.x / 5, p.y / 5, p.r / 5, 0, 2 * Math.PI);
        ctx.fillStyle = `rgb(${p.color[0]},${p.color[1]},${p.color[2]})`;
        ctx.fill();
        if (p.name === name) player = p;
      }

      for (const f of msg.foods) {
        ctx.beginPath();
        ctx.arc(f.x / 5, f.y / 5, 4, 0, 2 * Math.PI);
        ctx.fillStyle = "green";
        ctx.fill();
      }
    } else if (msg.type === "death") {
      alert("Вы умерли! Вас съел " + msg.killer);
      location.reload();
    }
  };

  document.addEventListener("keydown", (e) => {
    let dx = 0, dy = 0;
    if (e.key === "w") dy = -5;
    if (e.key === "s") dy = 5;
    if (e.key === "a") dx = -5;
    if (e.key === "d") dx = 5;
    ws.send(JSON.stringify({ type: "move", dx, dy }));
  });
</script>
</body>
</html>
"""

# === FLASK ОБРАБОТКА ===
flask_app = Flask(__name__)

@flask_app.route("/")
def index():
    return render_template_string(html_page)

# === FASTAPI ДЛЯ WEBSOCKET ===
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.mount("/", WSGIMiddleware(flask_app))

# ИГРОВАЯ ЛОГИКА
MAP_WIDTH, MAP_HEIGHT = 3000, 3000
players = {}
foods = [{"x": random.randint(0, MAP_WIDTH), "y": random.randint(0, MAP_HEIGHT)} for _ in range(150)]
connections = {}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    name = None
    try:
        data = await websocket.receive_json()
        if data["type"] == "join":
            name = data["name"]
            color = data["color"]
            players[name] = {
                "x": random.randint(0, MAP_WIDTH),
                "y": random.randint(0, MAP_HEIGHT),
                "r": 10,
                "name": name,
                "color": color,
            }
            connections[name] = websocket

        while True:
            data = await websocket.receive_json()
            if data["type"] == "move" and name in players:
                p = players[name]
                p["x"] = max(0, min(MAP_WIDTH, p["x"] + data["dx"]))
                p["y"] = max(0, min(MAP_HEIGHT, p["y"] + data["dy"]))

                # Еда
                eaten = []
                for f in foods:
                    dist = ((p["x"] - f["x"]) ** 2 + (p["y"] - f["y"]) ** 2) ** 0.5
                    if dist < p["r"]:
                        p["r"] += 1
                        eaten.append(f)
                for f in eaten:
                    foods.remove(f)
                    foods.append({"x": random.randint(0, MAP_WIDTH), "y": random.randint(0, MAP_HEIGHT)})

                # Съедание других игроков
                for other_name, other in list(players.items()):
                    if other_name != name:
                        dist = ((p["x"] - other["x"]) ** 2 + (p["y"] - other["y"]) ** 2) ** 0.5
                        if dist < p["r"] and p["r"] > other["r"] + 5:
                            p["r"] += int(other["r"] * 0.5)
                            del players[other_name]
                            try:
                                await connections[other_name].send_json({"type": "death", "killer": name})
                                await connections[other_name].close()
                            except:
                                pass
                            del connections[other_name]

            # Рассылаем обновления
            msg = {
                "type": "update",
                "players": players,
                "foods": foods
            }
            for ws in connections.values():
                try:
                    await ws.send_json(msg)
                except:
                    pass

    except WebSocketDisconnect:
        if name in players: del players[name]
        if name in connections: del connections[name]
    except Exception as e:
        print("Ошибка:", e)
        if name in players: del players[name]
        if name in connections: del connections[name]

# === ЗАПУСК ===
if __name__ == "__main__":
    uvicorn.run("app:main", host="0.0.0.0", port=8000, reload=True)
