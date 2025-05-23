from flask import Flask, request
from flask_socketio import SocketIO, emit, disconnect
import eventlet
import threading
import time

eventlet.monkey_patch()

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

players = {}
food = [{'x': 100, 'y': 100}, {'x': 400, 'y': 300}, {'x': 200, 'y': 400}]

@socketio.on('connect')
def handle_connect():
    print(f'[SERVER] Клиент подключился: {request.sid}')

@socketio.on('join')
def handle_join(data):
    name = data.get('name')
    print(f'[SERVER] Join от игрока: {name}')
    if not name or name in players:
        emit('error', {'message': 'Имя уже используется или некорректно'})
        disconnect()
        return
    players[name] = {'x': 0, 'y': 0, 'r': 15}
    emit('join_success', {'food': food})
    print(f'[SERVER] Игрок {name} добавлен')

@socketio.on('update')
def handle_update(data):
    name = data.get('name')
    if name in players:
        players[name].update({
            'x': data.get('x', players[name]['x']),
            'y': data.get('y', players[name]['y']),
            'r': data.get('r', players[name]['r']),
        })

@socketio.on('eat')
def handle_eat(data):
    eater = data.get('eater')
    eaten = data.get('eaten')
    if eater in players and eaten in players:
        if players[eater]['r'] > players[eaten]['r']:
            players[eater]['r'] += players[eaten]['r'] // 2
            del players[eaten]
            emit('killed', {'victim': eaten, 'killer': eater}, broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    print(f'[SERVER] Клиент отключился: {request.sid}')
    # Удалить игрока по sid нет, нужен поиск по players, можно усложнить если надо.

def game_update_loop():
    while True:
        socketio.emit('state', {'players': players, 'food': food})
        socketio.sleep(1/30)

if __name__ == '__main__':
    socketio.start_background_task(game_update_loop)
    socketio.run(app, host='0.0.0.0', port=5000)
