from flask import Flask, request
from flask_socketio import SocketIO, emit, disconnect
import random
import eventlet

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

players = {}
food = [{'x': random.randint(0, 2000), 'y': random.randint(0, 2000)} for _ in range(100)]

@socketio.on('connect')
def handle_connect():
    print(f'[SERVER] Подключился клиент: {request.sid}')

@socketio.on('join')
def handle_join(data):
    sid = request.sid
    name = data.get('name')

    if not name or name in players:
        emit('error', {'message': 'Имя уже используется или некорректно'})
        disconnect()
        return

    players[name] = {
        'x': 1000,
        'y': 1000,
        'r': 15,
        'sid': sid
    }

    print(f'[SERVER] {name} присоединился к игре')
    emit('join_success', {'food': food}, to=sid)
    emit('player_joined', {'name': name})

@socketio.on('update')
def handle_update(data):
    name = data.get('name')
    if name in players:
        players[name]['x'] = data.get('x', players[name]['x'])
        players[name]['y'] = data.get('y', players[name]['y'])
        players[name]['r'] = data.get('r', players[name]['r'])

@socketio.on('eat')
def handle_eat(data):
    eater = data.get('eater')
    eaten = data.get('eaten')
    if eater in players and eaten in players:
        if players[eater]['r'] > players[eaten]['r']:
            players[eater]['r'] += players[eaten]['r'] // 2
            emit('killed', {'killer': eater, 'victim': eaten}, to=players[eaten]['sid'])
            del players[eaten]
            print(f"[SERVER] {eater} съел {eaten}")

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    name_to_remove = None
    for name, info in players.items():
        if info['sid'] == sid:
            name_to_remove = name
            break
    if name_to_remove:
        del players[name_to_remove]
        print(f'[SERVER] {name_to_remove} отключился')
        emit('player_left', {'name': name_to_remove})

def update_loop():
    while True:
        socketio.sleep(0.05)
        emit('state', {
            'players': {name: {'x': p['x'], 'y': p['y'], 'r': p['r']} for name, p in players.items()},
            'food': food
        })

if __name__ == '__main__':
    print('[SERVER] Сервер запущен...')
    socketio.start_background_task(update_loop)
    socketio.run(app, host='0.0.0.0', port=10000)
