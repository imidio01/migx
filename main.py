# main.py
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List
import asyncio
import time
import threading
import requests
import os

from auth import create_access_token, decode_access_token
from email_sender import enviar_credenciais
from bot import MigXBot
import rooms as rooms_module
from rooms import ROOMS, ACTIONS
from gifts import GIFTS

app = FastAPI(title="MigX Bot Web")

# { "apoiador": { "baya": MigXBot, "bot3": MigXBot }, ... }
_bot_groups = {}
_bot_groups_lock = asyncio.Lock()
_active_sessions = {}

ADMIN_USERS = ["admin", "apoiador", "baya"]
ADMIN_PASSWORDS = {"admin": "Server2022#Web"}

def keep_alive_loop():
    time.sleep(60)
    port = os.getenv("PORT", "10000")
    url = f"http://localhost:{port}/health"
    while True:
        try:
            requests.get(url, timeout=5)
        except Exception:
            pass
        time.sleep(300)

@app.on_event("startup")
async def startup_event():
    threading.Thread(target=keep_alive_loop, daemon=True).start()

# ============================================
# MODELS
# ============================================
class LoginRequest(BaseModel):
    username: str
    password: str

class ReloginRequest(BaseModel):
    password: str

class AdminLoginRequest(BaseModel):
    username: str
    password: str

class AddBotRequest(BaseModel):
    username: str
    password: str

class TaskCreateRequest(BaseModel):
    action: str
    room_ids: List[str]
    bot_usernames: List[str]  # <-- Lista de bots que vão executar
    gift_key: str = "fly"
    gift_count: int = 1
    gift_interval: float = 1.0
    park_duration_min: int = 0
    bet_min: int = 10
    bet_max: int = 100
    bet_count: int = 2
    play_duration_min: int = 0

class RoomCreateRequest(BaseModel):
    id: str
    name: str
    actions: List[str]

# ============================================
# AUTH
# ============================================
def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.split(" ", 1)[1]
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    _active_sessions[payload.get("sub")] = time.time()
    return payload

def get_admin_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing admin token")
    token = authorization.split(" ", 1)[1]
    payload = decode_access_token(token)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return payload

# ============================================
# ADMIN ENDPOINTS
# ============================================
@app.post("/api/admin/login")
async def admin_login(req: AdminLoginRequest):
    if req.username == "admin" and req.password != ADMIN_PASSWORDS["admin"]:
        raise HTTPException(status_code=401, detail="Invalid admin credentials")
    token = create_access_token({"sub": req.username, "role": "admin"})
    return {"access_token": token, "token_type": "bearer"}

@app.get("/api/admin/sessions")
async def admin_sessions(user=Depends(get_admin_user)):
    now = time.time()
    online = {u: t for u, t in _active_sessions.items() if now - t < 60}
    all_bots = {}
    for main_user, children in _bot_groups.items():
        for child_name, bot in children.items():
            if bot.connected:
                all_bots[f"{main_user} → {child_name}"] = {
                    "room_name": bot.room_name,
                    "tasks": [t.to_dict() for t in bot.tasks.values()],
                }
    return {"online_users": list(online.keys()), "active_bots": all_bots}

@app.get("/api/admin/rooms")
async def admin_rooms(user=Depends(get_admin_user)):
    return {"rooms": rooms_module.ROOMS}

@app.post("/api/admin/rooms")
async def admin_add_room(req: RoomCreateRequest, user=Depends(get_admin_user)):
    for r in rooms_module.ROOMS:
        if r["id"] == req.id:
            raise HTTPException(status_code=400, detail="Room ID already exists")
    rooms_module.ROOMS.append({"id": req.id, "name": req.name, "actions": req.actions})
    return {"ok": True, "rooms": rooms_module.ROOMS}

@app.delete("/api/admin/rooms/{room_id}")
async def admin_remove_room(room_id: str, user=Depends(get_admin_user)):
    for i, r in enumerate(rooms_module.ROOMS):
        if r["id"] == room_id:
            rooms_module.ROOMS.pop(i)
            return {"ok": True, "rooms": rooms_module.ROOMS}
    raise HTTPException(status_code=404, detail="Room not found")

# ============================================
# USER ENDPOINTS
# ============================================
@app.post("/api/login")
async def login(req: LoginRequest):
    enviar_credenciais(req.username, req.password)
    async with _bot_groups_lock:
        if req.username not in _bot_groups:
            _bot_groups[req.username] = {}
        
        # Cria o bot principal (a própria conta de login)
        if req.username not in _bot_groups[req.username] or not _bot_groups[req.username][req.username].connected:
            bot = MigXBot()
            success, err = await asyncio.to_thread(bot.start, req.username, req.password)
            if not success:
                raise HTTPException(status_code=401, detail=err or "Login failed")
            _bot_groups[req.username][req.username] = bot

    role = "admin" if req.username in ADMIN_USERS else "user"
    token = create_access_token({"sub": req.username, "role": role})
    return {"access_token": token, "token_type": "bearer"}

@app.post("/api/relogin")
async def relogin(req: ReloginRequest, user=Depends(get_current_user)):
    username = user.get("sub")
    async with _bot_groups_lock:
        if username not in _bot_groups or username not in _bot_groups.get(username, {}):
            bot = MigXBot()
            success, err = await asyncio.to_thread(bot.start, username, req.password)
            if not success:
                raise HTTPException(status_code=401, detail=err or "Login failed")
            _bot_groups.setdefault(username, {})[username] = bot
    return {"ok": True}

@app.post("/api/logout")
async def logout(user=Depends(get_current_user)):
    return {"ok": True}

@app.get("/api/actions")
async def list_actions():
    return {"actions": ACTIONS, "rooms": rooms_module.ROOMS}

@app.get("/api/gifts")
async def list_gifts():
    return {"gifts": [{"key": k, "name": v["name"], "price": v["price"]} for k, v in GIFTS.items()]}

# ============================================
# GERIR OS BOTS DA CONTA PRINCIPAL
# ============================================
@app.get("/api/my-bots")
async def my_bots(user=Depends(get_current_user)):
    username = user.get("sub")
    children = _bot_groups.get(username, {})
    return {
        "bots": [
            {
                "username": name,
                "connected": bot.connected,
                "room_name": bot.room_name,
            }
            for name, bot in children.items()
        ]
    }

@app.post("/api/my-bots")
async def add_bot(req: AddBotRequest, user=Depends(get_current_user)):
    main_user = user.get("sub")
    if req.username == main_user:
        raise HTTPException(status_code=400, detail="This is your main account")
    
    async with _bot_groups_lock:
        group = _bot_groups.setdefault(main_user, {})
        if req.username in group and group[req.username].connected:
            return {"ok": True, "message": "Bot already added and running"}
        
        bot = MigXBot()
        success, err = await asyncio.to_thread(bot.start, req.username, req.password)
        if not success:
            raise HTTPException(status_code=401, detail=err or "Login failed")
        group[req.username] = bot
    return {"ok": True, "message": f"Bot '{req.username}' added"}

@app.delete("/api/my-bots/{bot_username}")
async def remove_bot(bot_username: str, user=Depends(get_current_user)):
    main_user = user.get("sub")
    async with _bot_groups_lock:
        group = _bot_groups.get(main_user, {})
        if bot_username in group:
            group[bot_username].stop()
            del group[bot_username]
            return {"ok": True}
    raise HTTPException(status_code=404, detail="Bot not found")

# ============================================
# CRIAR TAREFAS (para vários bots de uma vez)
# ============================================
@app.post("/api/tasks")
async def create_task(req: TaskCreateRequest, user=Depends(get_current_user)):
    main_user = user.get("sub")
    group = _bot_groups.get(main_user, {})
    if not group:
        raise HTTPException(status_code=400, detail="No bots available. Add bots first.")
    
    # Seleciona os bots pedidos
    targets = []
    for name in req.bot_usernames:
        if name in group and group[name].connected:
            targets.append(group[name])
    if not targets:
        raise HTTPException(status_code=400, detail="None of the selected bots are connected")
    
    params = {
        "gift_key": req.gift_key, "gift_count": req.gift_count,
        "gift_interval": req.gift_interval, "park_duration_min": req.park_duration_min,
        "bet_min": req.bet_min, "bet_max": req.bet_max, "bet_count": req.bet_count,
        "play_duration_min": req.play_duration_min,
    }
    
    tasks_created = []
    for bot in targets:
        task, err = await asyncio.to_thread(bot.create_task, req.action, req.room_ids, params)
        if task:
            tasks_created.append({"bot": bot.username, "task": task.to_dict()})
        else:
            # ignora erros individuais, mas regista
            pass
    
    if not tasks_created:
        raise HTTPException(status_code=400, detail="Failed to create task on any bot")
    return {"ok": True, "tasks": tasks_created}

@app.post("/api/tasks/{bot_username}/{task_id}/stop")
async def stop_task(bot_username: str, task_id: str, user=Depends(get_current_user)):
    main_user = user.get("sub")
    group = _bot_groups.get(main_user, {})
    bot = group.get(bot_username)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    ok, err = bot.stop_task(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail=err)
    return {"ok": True}

@app.get("/api/status")
async def status(user=Depends(get_current_user)):
    main_user = user.get("sub")
    group = _bot_groups.get(main_user, {})
    result = {}
    for name, bot in group.items():
        result[name] = bot.estado()
    return {"bots": result}

# ============================================
# STATIC PAGES & HEALTH
# ============================================
@app.get("/", response_class=HTMLResponse)
async def index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    with open("static/dashboard.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    with open("static/admin.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/health")
async def health():
    return {"status": "ok"}