from flask import Flask, request
from flask_socketio import SocketIO, emit, disconnect
import time, threading, random

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins='*')

players = {}
food = []
FOOD_COUNT = 100

def spawn_food():
    for _ in range(FOOD_COUNT - len(food)):
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
        'last_seen': time.time()
    }
    emit('join_success', {'food': food})
    print(f'{name} joined.')

@socketio.on('update')
def on_update(data):
    name = data['name']
    if name not in players:
        return
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
            emit('killed', {'killer': eater, 'victim': eaten}, broadcast=True)

@socketio.on('disconnect')
def on_disconnect():
    for name, player in list(players.items()):
        if request.sid == player.get('sid'):
            del players[name]

def cleanup():
    while True:
        now = time.time()
        for name in list(players):
            if now - players[name]['last_seen'] > 10:
                print(f"{name} timed out.")
                del players[name]
        spawn_food()
        socketio.emit('state', {'players': players, 'food': food})
        time.sleep(0.05)

if __name__ == '__main__':
    threading.Thread(target=cleanup, daemon=True).start()
    socketio.run(app, host='0.0.0.0', port=5000)
