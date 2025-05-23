# server.py
import eventlet
eventlet.monkey_patch()

from flask import Flask
from flask_socketio import SocketIO, emit
import time, random

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

players = {}
foods = []
MAP_WIDTH, MAP_HEIGHT = 2000, 2000
MAX_FOOD = 150

def spawn_food():
    while len(foods) < MAX_FOOD:
        foods.append({
            'x': random.randint(0, MAP_WIDTH),
            'y': random.randint(0, MAP_HEIGHT),
            'size': 5
        })

@socketio.on('connect')
def handle_connect():
    print("Client connected.")

@socketio.on('join')
def handle_join(data):
    name = data['name']
    if name in players:
        emit('join_response', {'success': False})
    else:
        players[name] = {
            'x': random.randint(100, MAP_WIDTH - 100),
            'y': random.randint(100, MAP_HEIGHT - 100),
            'size': 10,
            'last_active': time.time()
        }
        emit('join_response', {'success': True})
        print(f"{name} joined.")

@socketio.on('update')
def handle_update(data):
    name = data['name']
    if name not in players:
        return

    players[name]['x'] = data['x']
    players[name]['y'] = data['y']
    players[name]['size'] = data['size']
    players[name]['last_active'] = time.time()

    # Проверка еды
    px, py, pr = data['x'], data['y'], data['size']
    for f in foods[:]:
        fx, fy = f['x'], f['y']
        if (px - fx) ** 2 + (py - fy) ** 2 < (pr + f['size']) ** 2:
            foods.remove(f)
            players[name]['size'] += 1

    # Проверка столкновений с другими игроками
    for other_name, other in list(players.items()):
        if other_name == name:
            continue
        ox, oy, os = other['x'], other['y'], other['size']
        if (px - ox) ** 2 + (py - oy) ** 2 < (pr + os) ** 2:
            if pr > os + 5:
                players[name]['size'] += int(os / 2)
                del players[other_name]
                emit('death', {'killed_by': name}, to=other_name)

@socketio.on('disconnect')
def handle_disconnect():
    print("Client disconnected.")
    # Игрок удалится по тайм-ауту в game_loop

def game_loop():
    while True:
        spawn_food()
        now = time.time()
        for name in list(players):
            if now - players[name]['last_active'] > 10:
                print(f"{name} timed out.")
                del players[name]
        socketio.emit('game_state', {
            'players': players,
            'foods': foods
        })
        socketio.sleep(0.05)

if __name__ == '__main__':
    socketio.start_background_task(game_loop)
    socketio.run(app, host='0.0.0.0', port=8080)
