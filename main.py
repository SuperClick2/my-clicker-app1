from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
import random
import logging

# Настройка логирования для Flask и SocketIO
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True)

players = {}
player_names = set()
WIDTH = 800
HEIGHT = 600
PLAYER_RADIUS = 15


def generate_random_position():
    return {'x': random.randint(PLAYER_RADIUS, WIDTH - PLAYER_RADIUS), 'y': random.randint(PLAYER_RADIUS, HEIGHT - PLAYER_RADIUS)}


def check_collision(x, y):
    return not (PLAYER_RADIUS <= x <= WIDTH - PLAYER_RADIUS and PLAYER_RADIUS <= y <= HEIGHT - PLAYER_RADIUS)


@socketio.on('connect')
def handle_connect():
    print('Client connected with session ID: ' + request.sid)  # Session ID
    logging.debug(f"Client connected: {request.sid}")


@socketio.on('new_player')
def handle_new_player(name):
    player_id = request.sid
    if name in player_names:
        emit('name_taken', room=player_id)
        logging.warning(f"Name taken: {name} - rejected connection from {player_id}")
        return

    player_names.add(name)
    position = generate_random_position()
    color = f'rgb({random.randint(0, 255)}, {random.randint(0, 255)}, {random.randint(0, 255)})'

    players[player_id] = {'name': name, 'x': position['x'], 'y': position['y'],
                          'color': color, 'id': player_id}  # Add 'id' here

    # Отправляем только этому клиенту его данные
    emit('init_player', players[player_id], room=player_id)
    logging.debug(f"Sent init_player to {player_id}: {players[player_id]}")

    # Отправляем этому клиенту список текущих игроков
    emit('current_players', players, room=player_id)
    logging.debug(f"Sent current_players to {player_id}: {players}")

    # Отправляем всем остальным информацию о новом игроке
    emit('new_player_joined', players[player_id], broadcast=True, include_self=False)
    logging.info(f"New player {name} joined - id: {player_id}, position: {position}")


@socketio.on('move')
def handle_move(data):
    player_id = request.sid
    x = data.get('x')
    y = data.get('y')

    if x is None or y is None:
        logging.warning(f"Invalid move data received from {player_id}: {data}")
        return

    if check_collision(x, y):
        emit('invalid_move', room=player_id)
        logging.debug(f"Invalid move attempted by {player_id} to x={x}, y={y} (collision)")
        return

    if player_id in players:
        players[player_id]['x'] = x
        players[player_id]['y'] = y
        emit('player_moved', {'id': player_id, 'x': x, 'y': y}, broadcast=True, include_self=False)
        logging.debug(f"Player {player_id} moved to x={x}, y={y}")
    else:
        logging.warning(f"Move received from unknown player {player_id}")


@socketio.on('disconnect')
def handle_disconnect():
    player_id = request.sid
    if player_id in players:
        name = players[player_id]['name']
        del players[player_id]
        player_names.remove(name)
        emit('player_left', player_id, broadcast=True)
        logging.info(f"Player {name} disconnected - id: {player_id}")


@app.route('/')
def index():
    return render_template('index.html')


if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)
