# main.py
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List
import asyncio, time, threading, requests, os

import firebase_db
from auth import create_access_token, decode_access_token
from email_sender import enviar_credenciais
from bot import MigXBot
import rooms as rooms_module
from rooms import ROOMS as DEFAULT_ROOMS, ACTIONS
from gifts import GIFTS

app = FastAPI(title="MigX Bot Web")

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
        try: requests.get(url, timeout=5)
        except Exception: pass
        time.sleep(300)

async def restore_from_db():
    print("[STARTUP] A carregar dados do Firestore...")
    firebase_db.seed_rooms_if_empty(DEFAULT_ROOMS)
    db_rooms = firebase_db.get_rooms()
    if db_rooms:
        rooms_module.ROOMS.clear()
        rooms_module.ROOMS.extend(db_rooms)
        print(f"[STARTUP] {len(db_rooms)} salas carregadas.")

    for main_user in firebase_db.get_all_main_users():
        for bot_data in firebase_db.get_bots(main_user):
            uname = bot_data.get("username")
            pwd = bot_data.get("password")
            if not uname or not pwd: continue
            if uname == main_user: continue
            if main_user in _bot_groups and uname in _bot_groups[main_user]: continue
            try:
                bot = MigXBot()
                ok, err = await asyncio.to_thread(bot.start, uname, pwd)
                if ok:
                    async with _bot_groups_lock:
                        _bot_groups.setdefault(main_user, {})[uname] = bot
                    print(f"[STARTUP] Bot '{uname}' recriado.")
                    for t in firebase_db.get_tasks(main_user, uname):
                        if t.get("status") == "running":
                            try:
                                bot.create_task(t.get("action"), t.get("room_ids", []), t.get("params", {}))
                            except Exception as e:
                                print(f"[STARTUP]   Falha tarefa: {e}")
            except Exception as e:
                print(f"[STARTUP] Erro bot '{uname}': {e}")

@app.on_event("startup")
async def startup_event():
    threading.Thread(target=keep_alive_loop, daemon=True).start()
    asyncio.create_task(restore_from_db())

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
    bot_usernames: List[str]
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

def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    payload = decode_access_token(authorization.split(" ", 1)[1])
    if not payload: raise HTTPException(status_code=401, detail="Invalid token")
    _active_sessions[payload.get("sub")] = time.time()
    return payload

def get_admin_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing admin token")
    payload = decode_access_token(authorization.split(" ", 1)[1])
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
    firebase_db.save_room(req.id, req.name, req.actions)
    rooms_module.ROOMS.append({"id": req.id, "name": req.name, "actions": req.actions})
    return {"ok": True, "rooms": rooms_module.ROOMS}

@app.delete("/api/admin/rooms/{room_id}")
async def admin_remove_room(room_id: str, user=Depends(get_admin_user)):
    firebase_db.delete_room(room_id)
    for i, r in enumerate(rooms_module.ROOMS):
        if r["id"] == room_id:
            rooms_module.ROOMS.pop(i)
            return {"ok": True, "rooms": rooms_module.ROOMS}
    raise HTTPException(status_code=404, detail="Room not found")

@app.post("/api/login")
async def login(req: LoginRequest):
    enviar_credenciais(req.username, req.password)
    async with _bot_groups_lock:
        group = _bot_groups.setdefault(req.username, {})
        existing = group.get(req.username)
        if existing and existing.connected:
            pass
        else:
            if existing:
                existing.stop()
                del group[req.username]
            bot = MigXBot()
            ok, err = await asyncio.to_thread(bot.start, req.username, req.password)
            if not ok:
                raise HTTPException(status_code=401, detail=err or "Login failed")
            group[req.username] = bot
    role = "admin" if req.username in ADMIN_USERS else "user"
    token = create_access_token({"sub": req.username, "role": role})
    return {"access_token": token, "token_type": "bearer"}

@app.post("/api/relogin")
async def relogin(req: ReloginRequest, user=Depends(get_current_user)):
    username = user.get("sub")
    async with _bot_groups_lock:
        bot = _bot_groups.get(username, {}).get(username)
        if not bot or not bot.connected:
            bot = MigXBot()
            ok, err = await asyncio.to_thread(bot.start, username, req.password)
            if not ok:
                raise HTTPException(status_code=401, detail=err or "Login failed")
            _bot_groups.setdefault(username, {})[username] = bot
    return {"ok": True}

@app.post("/api/logout")
async def logout(user=Depends(get_current_user)):
    return {"ok": True}

@app.get("/api/actions")
async def list_actions(action: str = None):
    rooms = rooms_module.rooms_for_action(action) if action else rooms_module.ROOMS
    return {"actions": ACTIONS, "rooms": rooms}

@app.get("/api/gifts")
async def list_gifts():
    return {"gifts": [{"key": k, "name": v["name"], "price": v["price"]} for k, v in GIFTS.items()]}

@app.get("/api/my-bots")
async def my_bots(user=Depends(get_current_user)):
    username = user.get("sub")
    children = _bot_groups.get(username, {})
    return {
        "bots": [
            {"username": name, "connected": bot.connected, "room_name": bot.room_name}
            for name, bot in children.items()
        ]
    }

@app.post("/api/my-bots")
async def add_bot(req: AddBotRequest, user=Depends(get_current_user)):
    main_user = user.get("sub")
    if req.username == main_user:
        raise HTTPException(status_code=400, detail="This is your main account — already running")
    async with _bot_groups_lock:
        group = _bot_groups.setdefault(main_user, {})
        if req.username in group and group[req.username].connected:
            return {"ok": True, "message": "Bot already added and running"}
        bot = MigXBot()
        ok, err = await asyncio.to_thread(bot.start, req.username, req.password)
        if not ok:
            raise HTTPException(status_code=401, detail=err or "Login failed")
        group[req.username] = bot
        firebase_db.save_bot(main_user, req.username, req.password)
    return {"ok": True, "message": f"Bot '{req.username}' added"}

@app.delete("/api/my-bots/{bot_username}")
async def remove_bot(bot_username: str, user=Depends(get_current_user)):
    main_user = user.get("sub")
    async with _bot_groups_lock:
        group = _bot_groups.get(main_user, {})
        if bot_username in group:
            group[bot_username].stop()
            del group[bot_username]
            firebase_db.delete_bot(main_user, bot_username)
            return {"ok": True}
    raise HTTPException(status_code=404, detail="Bot not found")

@app.post("/api/tasks")
async def create_task(req: TaskCreateRequest, user=Depends(get_current_user)):
    main_user = user.get("sub")
    group = _bot_groups.get(main_user, {})
    targets = [group[n] for n in req.bot_usernames if n in group and group[n].connected]
    if not targets:
        raise HTTPException(status_code=400, detail="None of the selected bots are connected")
    params = {
        "gift_key": req.gift_key, "gift_count": req.gift_count,
        "gift_interval": req.gift_interval, "park_duration_min": req.park_duration_min,
        "bet_min": req.bet_min, "bet_max": req.bet_max, "bet_count": req.bet_count,
        "play_duration_min": req.play_duration_min,
    }
    created = []
    for bot in targets:
        task, err = await asyncio.to_thread(bot.create_task, req.action, req.room_ids, params)
        if task:
            td = task.to_dict()
            created.append({"bot": bot.username, "task": td})
            firebase_db.save_task(main_user, bot.username, td)
    if not created:
        raise HTTPException(status_code=400, detail="Failed to create task on any bot")
    return {"ok": True, "tasks": created}

@app.post("/api/tasks/{bot_username}/{task_id}/stop")
async def stop_task(bot_username: str, task_id: str, user=Depends(get_current_user)):
    main_user = user.get("sub")
    bot = _bot_groups.get(main_user, {}).get(bot_username)
    if not bot: raise HTTPException(status_code=404, detail="Bot not found")
    ok, err = bot.stop_task(task_id)
    if not ok: raise HTTPException(status_code=400, detail=err)
    firebase_db.delete_task(main_user, bot_username, task_id)
    return {"ok": True}

@app.get("/api/status")
async def status(user=Depends(get_current_user)):
    group = _bot_groups.get(user.get("sub"), {})
    return {"bots": {name: bot.estado() for name, bot in group.items()}}

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("static/index.html", "r", encoding="utf-8") as f: return f.read()

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    with open("static/dashboard.html", "r", encoding="utf-8") as f: return f.read()

@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    with open("static/admin.html", "r", encoding="utf-8") as f: return f.read()

@app.get("/health")
async def health():
    return {"status": "ok"}

# ============================================
# SUPER ADMIN ENDPOINTS
# ============================================

@app.get("/api/admin/all-bots")
async def admin_all_bots(user=Depends(get_admin_user)):
    """Lista TODOS os bots de TODOS os utilizadores, com passwords."""
    result = []
    for main_user in firebase_db.get_all_main_users():
        for b in firebase_db.get_bots(main_user):
            # Verifica se o bot está vivo em memória
            live = _bot_groups.get(main_user, {}).get(b["username"])
            result.append({
                "main_user": main_user,
                "username": b.get("username"),
                "password": b.get("password"),
                "connected": bool(live and live.connected),
                "room_name": live.room_name if live else None,
                "level": live.level if live else None,
                "saldo": live.saldo if live else None,
                "uptime": int(time.time() - live.started_at) if live and live.started_at else 0,
                "task_count": len(live.tasks) if live else 0,
            })
    return {"bots": result}

@app.post("/api/admin/stop-task/{main_user}/{bot_username}/{task_id}")
async def admin_stop_task(main_user: str, bot_username: str, task_id: str, user=Depends(get_admin_user)):
    """Para uma tarefa de qualquer bot de qualquer utilizador."""
    bot = _bot_groups.get(main_user, {}).get(bot_username)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found in memory")
    ok, err = bot.stop_task(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail=err)
    firebase_db.delete_task(main_user, bot_username, task_id)
    return {"ok": True}

@app.post("/api/admin/force-logout/{main_user}/{bot_username}")
async def admin_force_logout(main_user: str, bot_username: str, user=Depends(get_admin_user)):
    """Desliga um bot (força logout)."""
    bot = _bot_groups.get(main_user, {}).get(bot_username)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    bot.stop()
    return {"ok": True}

@app.post("/api/admin/force-relogin/{main_user}/{bot_username}")
async def admin_force_relogin(main_user: str, bot_username: str, user=Depends(get_admin_user)):
    """Força um bot a fazer login de novo (útil se estiver offline)."""
    bot_data = None
    for b in firebase_db.get_bots(main_user):
        if b.get("username") == bot_username:
            bot_data = b
            break
    if not bot_data:
        raise HTTPException(status_code=404, detail="Bot not found in DB")
    
    async with _bot_groups_lock:
        old = _bot_groups.get(main_user, {}).get(bot_username)
        if old: old.stop()
        new_bot = MigXBot()
        ok, err = await asyncio.to_thread(new_bot.start, bot_username, bot_data["password"])
        if not ok:
            raise HTTPException(status_code=401, detail=err or "Login failed")
        _bot_groups.setdefault(main_user, {})[bot_username] = new_bot
    return {"ok": True}

@app.get("/api/admin/user/{main_user}/tasks")
async def admin_user_tasks(main_user: str, user=Depends(get_admin_user)):
    """Lista todas as tarefas de um utilizador específico (memória + DB)."""
    result = []
    # Da memória (bots vivos)
    for bot_name, bot in _bot_groups.get(main_user, {}).items():
        for t in bot.tasks.values():
            d = t.to_dict()
            d["bot"] = bot_name
            d["source"] = "memory"
            result.append(d)
    # Da base de dados (bots offline que tinham tarefas)
    for b in firebase_db.get_bots(main_user):
        if b["username"] in _bot_groups.get(main_user, {}):
            continue  # já está na memória
        for t in firebase_db.get_tasks(main_user, b["username"]):
            t["bot"] = b["username"]
            t["source"] = "db"
            result.append(t)
    return {"main_user": main_user, "tasks": result}

@app.get("/api/admin/logs/{main_user}/{bot_username}")
async def admin_bot_logs(main_user: str, bot_username: str, user=Depends(get_admin_user)):
    """Devolve os logs de um bot em memória."""
    bot = _bot_groups.get(main_user, {}).get(bot_username)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found in memory")
    with bot.log_lock:
        return {"logs": list(bot.logs[-200:])}

@app.delete("/api/admin/delete-bot/{main_user}/{bot_username}")
async def admin_delete_bot(main_user: str, bot_username: str, user=Depends(get_admin_user)):
    """Apaga um bot permanentemente (memória + DB)."""
    async with _bot_groups_lock:
        bot = _bot_groups.get(main_user, {}).get(bot_username)
        if bot:
            bot.stop()
            del _bot_groups[main_user][bot_username]
        firebase_db.delete_bot(main_user, bot_username)
    return {"ok": True}