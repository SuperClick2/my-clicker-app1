import pygame
import threading
import websocket
import json
import time
import random

WIDTH, HEIGHT = 800, 600
FPS = 60

SERVER_URL = "wss://my-clicker-app1.onrender.com/ws"

pygame.init()
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Agar Multiplayer")
font = pygame.font.SysFont("arial", 24)

player = {"x": 0, "y": 0, "r": 10, "name": ""}
players = {}
foods = []
portals = []
dead = False
killer = ""

eat_message = None
eat_message_time = 0

kill_message = None
kill_message_time = 0

clock = pygame.time.Clock()

WHITE = (255, 255, 255)
GREEN = (0, 255, 0)
BLUE = (100, 100, 255)
RED = (255, 50, 50)
GOLD = (255, 215, 0)
BLACK = (0, 0, 0)

ws = None
connected = False

# Загрузка изображений порталов
try:
    portal_img = pygame.image.load("ortal.png").convert_alpha()
    portal_img = pygame.transform.scale(portal_img, (40, 40))
    portal2_img = pygame.image.load("ortal2.png").convert_alpha()
    portal2_img = pygame.transform.scale(portal2_img, (40, 40))
except Exception as e:
    print("Ошибка загрузки изображений порталов:", e)
    portal_img = None
    portal2_img = None

def ws_thread(name):
    global ws, player, players, foods, dead, killer, connected, portals, eat_message, eat_message_time, kill_message, kill_message_time
    try:
        ws = websocket.WebSocket()
        ws.connect(SERVER_URL)
        ws.send(json.dumps({"type": "join", "name": name}))
        connected = True

        while True:
            msg = json.loads(ws.recv())
            if msg["type"] == "update":
                players = msg["players"]
                foods = msg["foods"]
                portals = msg.get("portals", [])
                if name in players:
                    player = players[name]

            elif msg["type"] == "death":
                dead = True
                killer = msg["killer"]

            elif msg["type"] == "eat":
                # Сообщение что кто-то съел кого-то
                killer_name = msg["killer"]
                victim_name = msg["victim"]
                kill_message = f"{killer_name} съел {victim_name}"
                kill_message_time = time.time()

            elif msg["type"] == "food_eaten":
                eater = msg["name"]
                if eater == player["name"]:
                    eat_message = "Ты съел еду!"
                    eat_message_time = time.time()

            elif msg["type"] == "error":
                print("Ошибка сервера:", msg.get("message", ""))
                dead = True

    except Exception as e:
        print("Disconnected:", e)
        connected = False


def draw_game():
    global eat_message, kill_message

    screen.fill(WHITE)
    cam_x = player["x"] - WIDTH // 2
    cam_y = player["y"] - HEIGHT // 2

    # Рисуем границы чёрной линией
    pygame.draw.rect(screen, BLACK, (-cam_x, -cam_y, 2000, 2000), 3)

    # Рисуем еду
    for f in foods:
        pygame.draw.circle(screen, GREEN, (int(f["x"] - cam_x), int(f["y"] - cam_y)), 5)

    # Рисуем порталы
    for p in portals:
        px, py = p["x"], p["y"]
        pos = (int(px - cam_x - 20), int(py - cam_y - 20))  # центрируем по середине 40x40
        if p["type"] == "mass":
            if portal_img:
                screen.blit(portal_img, pos)
            else:
                pygame.draw.rect(screen, (0, 255, 255), (*pos, 40, 40))  # голубой квадрат если нет картинки
        elif p["type"] == "teleport":
            if portal2_img:
                screen.blit(portal2_img, pos)
            else:
                pygame.draw.rect(screen, (255, 0, 255), (*pos, 40, 40))  # фиолетовый квадрат если нет картинки

    # Лидерборд - топ 10 по массе
    sorted_players = sorted(players.values(), key=lambda p: p["r"], reverse=True)
    leaderboard = sorted_players[:10]

    # Рисуем в правом верхнем углу
    x_leaderboard = WIDTH - 200
    y_leaderboard = 10
    header = font.render("Leaderstats (Масса)", True, BLACK)
    screen.blit(header, (x_leaderboard, y_leaderboard))
    y_leaderboard += 30

    for i, p in enumerate(leaderboard):
        color = GOLD if p["name"] == player["name"] and i == 0 else BLACK
        text = font.render(f"{i+1}. {p['name']}: {p['r']}", True, color)
        screen.blit(text, (x_leaderboard, y_leaderboard))
        y_leaderboard += 25

        # Аура вокруг игрока, если ты на первом месте
        if p["name"] == player["name"] and i == 0:
            # Золотая аура вокруг круга
            aura_radius = p["r"] + 7
            pos_x = int(p["x"] - cam_x)
            pos_y = int(p["y"] - cam_y)
            pygame.draw.circle(screen, GOLD, (pos_x, pos_y), aura_radius, 5)

    # Рисуем игроков
    for p in players.values():
        color = RED if p["name"] == player["name"] else BLUE
        pygame.draw.circle(screen, color, (int(p["x"] - cam_x), int(p["y"] - cam_y)), p["r"])
        text = font.render(p["name"], True, BLACK)
        screen.blit(text, (int(p["x"] - cam_x - text.get_width() / 2), int(p["y"] - cam_y - p["r"] - 20)))

    # Сообщение о съедании
    if kill_message and time.time() - kill_message_time < 3:
        kill_text = font.render(kill_message, True, RED)
        screen.blit(kill_text, (WIDTH // 2 - kill_text.get_width() // 2, 20))
    else:
        kill_message = None

    # Сообщение о поедании еды
    if eat_message and time.time() - eat_message_time < 2:
        eat_text = font.render(eat_message, True, GREEN)
        screen.blit(eat_text, (WIDTH // 2 - eat_text.get_width() // 2, 50))
    else:
        eat_message = None


def death_screen():
    screen.fill(WHITE)
    text1 = font.render(f"Ты умер. Тебя съел {killer}", True, RED)
    screen.blit(text1, (WIDTH // 2 - text1.get_width() // 2, HEIGHT // 2 - 40))
    btn = font.render("В меню", True, BLUE)
    pygame.draw.rect(screen, (220, 220, 220), (WIDTH // 2 - 80, HEIGHT // 2 + 10, 160, 40))
    screen.blit(btn, (WIDTH // 2 - btn.get_width() // 2, HEIGHT // 2 + 20))
    return pygame.Rect(WIDTH // 2 - 80, HEIGHT // 2 + 10, 160, 40)


def menu_screen():
    name = ""
    input_active = True
    while True:
        screen.fill(WHITE)
        title = font.render("Введите имя:", True, (0, 0, 0))
        screen.blit(title, (WIDTH // 2 - 100, HEIGHT // 2 - 60))

        pygame.draw.rect(screen, (200, 200, 255), (WIDTH // 2 - 100, HEIGHT // 2 - 20, 200, 40))
        name_text = font.render(name, True, (0, 0, 0))
        screen.blit(name_text, (WIDTH // 2 - 90, HEIGHT // 2 - 10))

        btn = font.render("Играть", True, BLUE)
        pygame.draw.rect(screen, (220, 220, 220), (WIDTH // 2 - 60, HEIGHT // 2 + 40, 120, 40))
        screen.blit(btn, (WIDTH // 2 - btn.get_width() // 2, HEIGHT // 2 + 50))

        pygame.display.flip()
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                exit()
            elif event.type == pygame.KEYDOWN and input_active:
                if event.key == pygame.K_RETURN:
                    if name.strip():
                        return name.strip()
                elif event.key == pygame.K_BACKSPACE:
                    name = name[:-1]
                else:
                    if len(name) < 12 and event.unicode.isprintable():
                        name += event.unicode
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if WIDTH // 2 - 60 <= event.pos[0] <= WIDTH // 2 + 60 and HEIGHT // 2 + 40 <= event.pos[1] <= HEIGHT // 2 + 80:
                    if name.strip():
                        return name.strip()


def main():
    global dead, player, players, connected

    while True:
        name = menu_screen()
        dead = False
        player = {"x": 0, "y": 0, "r": 10, "name": name}
        players.clear()

        thread = threading.Thread(target=ws_thread, args=(name,), daemon=True)
        thread.start()

        while not connected:
            time.sleep(0.1)

        while not dead:
            keys = pygame.key.get_pressed()
            dx = dy = 0
            speed = 5
            if keys[pygame.K_w]: dy -= speed
            if keys[pygame.K_s]: dy += speed
            if keys[pygame.K_a]: dx -= speed
            if keys[pygame.K_d]: dx += speed
            if ws:
                try:
                    ws.send(json.dumps({"type": "move", "dx": dx, "dy": dy}))
                except:
                    pass

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    exit()

            draw_game()
            pygame.display.flip()
            clock.tick(FPS)

        # После смерти
        while True:
            btn_rect = death_screen()
            pygame.display.flip()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    exit()
                elif event.type == pygame.MOUSEBUTTONDOWN and btn_rect.collidepoint(event.pos):
                    connected = False
                    try:
                        if ws:
                            ws.close()
                    except:
                        pass
                    break
            else:
                continue
            break


if __name__ == "__main__":
    main()
