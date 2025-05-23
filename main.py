import eventlet
eventlet.monkey_patch()

from flask import Flask, request
from flask_socketio import SocketIO, emit
import time, random

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

players = {}
sid_to_name = {}
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
def on_connect():
    print(f"Client connected: {request.sid}")

@socketio.on('join')
def on_join(data):
    name = data['name']
    # Проверяем, есть ли уже игрок с таким именем и отключаем его, если да
    if name in players:
        old_sid = None
        for sid, n in sid_to_name.items():
            if n == name:
                old_sid = sid
                break
        if old_sid:
            print(f"Kick old player {name} with sid {old_sid}")
            socketio.server.disconnect(old_sid)

    # Регистрируем нового игрока
    players[name] = {
        'x': random.randint(100, MAP_WIDTH - 100),
        'y': random.randint(100, MAP_HEIGHT - 100),
        'size': 10,
        'last_active': time.time()
    }
    sid_to_name[request.sid] = name
    emit('join_response', {'success': True, 'name': name})
    print(f"{name} joined with sid {request.sid}")

@socketio.on('update')
def on_update(data):
    name = data.get('name')
    if not name or name not in players:
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
def on_disconnect():
    sid = request.sid
    name = sid_to_name.get(sid)
    if name:
        print(f"{name} disconnected, removing from players.")
        players.pop(name, None)
        sid_to_name.pop(sid, None)
    else:
        print(f"Unknown sid disconnected: {sid}")

def game_loop():
    while True:
        spawn_food()
        now = time.time()
        to_remove = []
        for name, p in players.items():
            if now - p['last_active'] > 10:
                print(f"Player {name} timed out, removing.")
                to_remove.append(name)
        for name in to_remove:
            players.pop(name, None)
            # Удаляем sid, связанный с этим именем
            sid_to_remove = None
            for sid, n in sid_to_name.items():
                if n == name:
                    sid_to_remove = sid
                    break
            if sid_to_remove:
                sid_to_name.pop(sid_to_remove, None)

        socketio.emit('game_state', {'players': players, 'foods': foods})
        socketio.sleep(0.05)

if __name__ == '__main__':
    socketio.start_background_task(game_loop)
    socketio.run(app, host='0.0.0.0', port=8080)
