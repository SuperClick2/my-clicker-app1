from flask import Flask, render_template
from flask_socketio import SocketIO, emit
import random
import time

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*")

# Игровые данные
players = {}
foods = []
game_width = 2000
game_height = 2000
food_count = 100
next_player_id = 1

# Генерация еды
def generate_food():
    global foods
    foods = [{
        'id': i,
        'x': random.randint(0, game_width),
        'y': random.randint(0, game_height),
        'size': random.randint(5, 10),
        'color': (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
    } for i in range(food_count)]

generate_food()

@socketio.on('connect')
def handle_connect():
    print('Client connected:', request.sid)

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected:', request.sid)
    sid = request.sid
    if sid in players:
        player_name = players[sid]['name']
        del players[sid]
        emit('player_disconnected', {'name': player_name}, broadcast=True)

@socketio.on('join_game')
def handle_join_game(data):
    global next_player_id
    
    name = data.get('name', '').strip()
    sid = request.sid
    
    # Проверка на уникальность имени
    if not name:
        emit('join_error', {'message': 'Имя не может быть пустым'})
        return
    
    for player in players.values():
        if player['name'].lower() == name.lower():
            emit('join_error', {'message': 'Имя уже занято'})
            return
    
    # Создание нового игрока
    player = {
        'id': next_player_id,
        'name': name,
        'x': random.randint(0, game_width),
        'y': random.randint(0, game_height),
        'size': 20,
        'color': (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)),
        'score': 0,
        'sid': sid
    }
    
    players[sid] = player
    next_player_id += 1
    
    # Отправляем новому игроку текущее состояние игры
    emit('init_game', {
        'player': player,
        'foods': foods,
        'players': [p for p in players.values() if p['sid'] != sid],
        'game_width': game_width,
        'game_height': game_height
    })
    
    # Сообщаем всем о новом игроке
    emit('new_player', player, broadcast=True)

@socketio.on('player_move')
def handle_player_move(data):
    sid = request.sid
    if sid not in players:
        return
    
    player = players[sid]
    
    # Обновляем позицию игрока
    player['x'] = data['x']
    player['y'] = data['y']
    
    # Проверяем столкновения с едой
    for food in foods[:]:
        dx = player['x'] - food['x']
        dy = player['y'] - food['y']
        distance = (dx**2 + dy**2)**0.5
        
        if distance < player['size']:
            player['size'] += food['size'] * 0.2
            player['score'] += 1
            foods.remove(food)
            emit('food_eaten', {'food_id': food['id']}, broadcast=True)
            
            # Добавляем новую еду
            new_food = {
                'id': max(f['id'] for f in foods) + 1 if foods else 0,
                'x': random.randint(0, game_width),
                'y': random.randint(0, game_height),
                'size': random.randint(5, 10),
                'color': (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
            }
            foods.append(new_food)
            emit('new_food', new_food, broadcast=True)
    
    # Проверяем столкновения с другими игроками
    for other_sid, other_player in players.items():
        if other_sid == sid:
            continue
        
        dx = player['x'] - other_player['x']
        dy = player['y'] - other_player['y']
        distance = (dx**2 + dy**2)**0.5
        
        # Если текущий игрок больше другого, он может его съесть
        if distance < player['size'] and player['size'] > other_player['size'] * 1.1:
            player['size'] += other_player['size'] * 0.2
            player['score'] += other_player['score']
            
            # Удаляем съеденного игрока
            del players[other_sid]
            socketio.emit('player_eaten', {
                'eater_id': player['id'],
                'eaten_id': other_player['id'],
                'eater_name': player['name'],
                'eaten_name': other_player['name']
            }, broadcast=True)
            
            # Отправляем сообщение о смерти съеденному игроку
            socketio.emit('you_died', {
                'killer_name': player['name']
            }, room=other_sid)
    
    # Отправляем обновленное состояние игрока всем
    emit('player_update', player, broadcast=True)

if __name__ == '__main__':
    print("Starting server on http://localhost:5000")
    socketio.run(app, host='0.0.0.0', port=5000)
