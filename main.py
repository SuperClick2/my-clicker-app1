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
def on_connect():
    print('[SERVER] Новый клиент подключился:', request.sid)

@socketio.on('join')
def on_join(data):
    sid = request.sid
    name = data.get('name')

    if name in players:
        emit('error', {'message': 'Имя уже используется'})
        disconnect()
        return

    players[name] = {
        'x': 1000,
        'y': 1000,
        'r': 15,
        'sid': sid
    }

    print(f'[SERVER] {name} присоединился')
    emit('join_success', {'food': food}, to=sid)

@socketio.on('update')
def on_update(data):
    name = data.get('name')
    if name in players:
        players[name]['x'] = data.get('x')
        players[name]['y'] = data.get('y')
        players[name]['r'] = data.get('r')
        # Отправить всем обновлённое состояние
        emit('state', {
            'players': {k: {'x': v['x'], 'y': v['y'], 'r': v['r']} for k, v in players.items()},
            'food': food
        }, broadcast=True)

@socketio.on('eat')
def on_eat(data):
    eater = data['eater']
    eaten = data['eaten']
    if eater in players and eaten in players:
        if players[eater]['r'] > players[eaten]['r']:
            players[eater]['r'] += players[eaten]['r'] // 2
            emit('killed', {'killer': eater, 'victim': eaten}, to=players[eaten]['sid'])
            del players[eaten]
            print(f"[SERVER] {eater} съел {eaten}")

@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    to_remove = None
    for name, pdata in players.items():
        if pdata['sid'] == sid:
            to_remove = name
            break
    if to_remove:
        del players[to_remove]
        print(f"[SERVER] {to_remove} отключился")

# Фоновый цикл обновления (опционально)
def update_loop():
    while True:
        socketio.sleep(0.05)
        emit('state', {
            'players': {k: {'x': v['x'], 'y': v['y'], 'r': v['r']} for k, v in players.items()},
            'food': food
        }, broadcast=True)

# Запуск сервера
if __name__ == '__main__':
    print("[SERVER] Запуск...")
    socketio.start_background_task(update_loop)
    socketio.run(app, host="0.0.0.0", port=10000)
