from flask import Flask, request
from flask_socketio import SocketIO, emit
import time, threading, random
import eventlet

eventlet.monkey_patch()

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='eventlet')

players = {}
food = []
FOOD_COUNT = 100

def spawn_food():
    while len(food) < FOOD_COUNT:
        food.append({
            "x": random.randint(0, 2000),
            "y": random.randint(0, 2000)
        })

@socketio.on('connect')
def on_connect():
    emit('connected', {'status': 'ok'})

@socketio.on('join')
def on_join(data):
    name = data['name']
    if name in players:
        emit('error', {'message': 'Имя уже занято'})
        return
    players[name] = {
        'x': random.randint(100, 1900),
        'y': random.randint(100, 1900),
        'r': 15,
        'last_seen': time.time(),
        'sid': request.sid
    }
    emit('join_success', {'food': food})
    print(f'[JOIN] {name}')
    socketio.emit('player_joined', {'name': name}, broadcast=True)

@socketio.on('update')
def on_update(data):
    name = data['name']
    if name in players:
        players[name]['x'] = data['x']
        players[name]['y'] = data['y']
        players[name]['r'] = data['r']
        players[name]['last_seen'] = time.time()

@socketio.on('eat')
def on_eat(data):
    eater = data['eater']
    eaten = data['eaten']
    if eater in players and eaten in players:
        if players[eater]['r'] > players[eaten]['r']:
            players[eater]['r'] += players[eaten]['r'] // 2
            del players[eaten]
            socketio.emit('killed', {'killer': eater, 'victim': eaten}, broadcast=True)
            print(f"[KILL] {eater} съел {eaten}")

@socketio.on('disconnect')
def on_disconnect():
    to_remove = None
    for name, p in players.items():
        if p['sid'] == request.sid:
            to_remove = name
            break
    if to_remove:
        print(f'[DISCONNECT] {to_remove}')
        del players[to_remove]

def cleanup():
    while True:
        now = time.time()
        for name in list(players):
            if now - players[name]['last_seen'] > 10:
                print(f"[TIMEOUT] {name}")
                del players[name]
        spawn_food()
        socketio.emit('state', {'players': players, 'food': food})
        eventlet.sleep(0.05)

@app.route('/')
def index():
    return "Server is running."

if __name__ == '__main__':
    threading.Thread(target=cleanup, daemon=True).start()
    socketio.run(app, host='0.0.0.0', port=10000)
