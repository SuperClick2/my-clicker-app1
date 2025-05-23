from flask import Flask, render_template
from flask_socketio import SocketIO, emit
import random
import threading

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*")  # Разрешаем кросс-доменные запросы
#socketio = SocketIO(app, cors_allowed_origins="https://my-clicker-app1.onrender.com")  # Укажите ваш домен

players = {}
player_names = set()
WIDTH = 800
HEIGHT = 600

def generate_random_position():
    return {'x': random.randint(0, WIDTH), 'y': random.randint(0, HEIGHT)}


def check_collision(x, y, radius=15):
    """Проверка столкновения с границами окна."""
    return not (radius <= x <= WIDTH - radius and radius <= y <= HEIGHT - radius)


@socketio.on('connect')
def handle_connect():
    print('Client connected')

@socketio.on('new_player')
def handle_new_player(name):
    if name in player_names:
        emit('name_taken')
        return

    player_names.add(name)
    player_id = request.sid  # ID клиента из сокета
    position = generate_random_position()

    players[player_id] = {'name': name, 'x': position['x'], 'y': position['y'], 'color': f'rgb({random.randint(0, 255)}, {random.randint(0, 255)}, {random.randint(0, 255)})'}

    emit('init_player', {'id': player_id, 'x': position['x'], 'y': position['y'], 'color': players[player_id]['color']}, room=player_id) # Инициализация для этого игрока
    emit('current_players', players, room=player_id)  # Отправляем текущих игроков ему
    emit('new_player_joined', players[player_id], broadcast=True, include_self=False)  # Сообщаем другим о новом игроке
    print(f'New player {name} joined with id {player_id}')


@socketio.on('move')
def handle_move(data):
    player_id = request.sid
    x = data['x']
    y = data['y']

    # Проверка на границы экрана
    if check_collision(x, y):
        # Вернуть игрока на предыдущую позицию или не давать двигаться.
        emit('invalid_move', room=player_id)
        return

    if player_id in players:
        players[player_id]['x'] = x
        players[player_id]['y'] = y
        emit('player_moved', {'id': player_id, 'x': x, 'y': y}, broadcast=True, include_self=False)  # Сообщаем другим об изменении позиции


@socketio.on('disconnect')
def handle_disconnect():
    player_id = request.sid
    if player_id in players:
        name = players[player_id]['name']
        del players[player_id]
        player_names.remove(name)
        emit('player_left', player_id, broadcast=True)
        print(f'Player {name} disconnected')


@app.route('/')
def index():
    return render_template('index.html')


if __name__ == '__main__':
    #socketio.run(app, debug=True, host='0.0.0.0', port=5000)
    socketio.run(app, debug=True, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)
