import asyncio
import socketio
from aiohttp import web

sio = socketio.AsyncServer(cors_allowed_origins='*')
app = web.Application()
sio.attach(app)

players = {}  # sid: {x, y, color}
WIDTH, HEIGHT = 800, 600

@sio.event
async def connect(sid, environ):
    print(f"✔️ Player connected: {sid}")
    players[sid] = {'x': WIDTH // 2, 'y': HEIGHT // 2, 'color': (255, 0, 0)}
    await sio.emit('players_update', players)

@sio.event
async def disconnect(sid):
    print(f"❌ Player disconnected: {sid}")
    players.pop(sid, None)
    await sio.emit('players_update', players)

@sio.event
async def move(sid, data):
    try:
        player = players.get(sid)
        if player:
            dx = int(data.get('dx', 0))
            dy = int(data.get('dy', 0))
            player['x'] = max(0, min(WIDTH, player['x'] + dx))
            player['y'] = max(0, min(HEIGHT, player['y'] + dy))
            await sio.emit('players_update', players)
    except Exception as e:
        print(f"⚠️ Error in move event from {sid}: {e}")

if __name__ == '__main__':
    web.run_app(app, port=5000)
