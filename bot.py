# bot.py
import socketio
import requests
import threading
import time
import uuid
import random
import re
from datetime import datetime
from rooms import find_room, is_event_driven
from gifts import get_gift_key

API_BASE = "https://api.migxapp.com"
SOCKET_URL = "https://api.migxapp.com"
HEARTBEAT_INTERVALO = 30

_room_join_lock = threading.Lock()

def fazer_login(username, password):
    url = f"{API_BASE}/trpc/auth.login?batch=1"
    payload = {"0": {"json": {"username": username, "password": password}}}
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        result = data[0]["result"]["data"]["json"] if isinstance(data, list) else data["result"]["data"]["json"]
        if result.get("loginState") == "authenticated":
            return {"token": result["token"], "user": result["user"]}
        return None
    except Exception:
        return None

def entrar_sala_http(token, room_id):
    url = f"{API_BASE}/trpc/room.join?batch=1"
    payload = {"0": {"json": {"roomId": room_id}}}
    headers = {"Content-Type": "application/json", "Accept": "application/json", "Authorization": f"Bearer {token}"}
    r = requests.post(url, json=payload, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()
    return data[0]["result"]["data"]["json"] if isinstance(data, list) else data["result"]["data"]["json"]

def enviar_mensagem_http(token, room_id, content):
    url = f"{API_BASE}/trpc/room.sendMessage?batch=1"
    payload = {
        "0": {
            "json": {"roomId": room_id, "content": content, "messageType": "text", "replyTo": None},
            "meta": {"values": {"replyTo": ["undefined"]}, "v": 1},
        }
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json", "Authorization": f"Bearer {token}"}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=10)
        if r.status_code >= 400:
            return False, r.text[:200]
        data = r.json()
        result = data[0]["result"]["data"]["json"] if isinstance(data, list) else data["result"]["data"]["json"]
        return result.get("success", False), result
    except Exception as e:
        return False, str(e)

class Task:
    _counter = 0
    def __init__(self, action, room_ids, room_names, params):
        Task._counter += 1
        self.id = f"t{Task._counter}-{uuid.uuid4().hex[:6]}"
        self.action = action
        self.room_ids = room_ids
        self.room_names = room_names
        self.params = params
        self.status = "running"
        self.started_at = time.time()
        self.stopped_at = None
        self.gifts_sent = 0
        self.gift_target = params.get("gift_count", 0) if action == "gift" else 0
        self.park_ends_at = None
        self.play_ends_at = None
        self.stop_event = threading.Event()

    def elapsed(self):
        end = self.stopped_at or time.time()
        return int(end - self.started_at)

    def to_dict(self):
        room_display = self.room_names[0] if len(self.room_names) == 1 else f"{len(self.room_names)} rooms (rotating)"
        return {
            "id": self.id, "action": self.action,
            "room_id": self.room_ids[0] if len(self.room_ids) == 1 else "multiple",
            "room_ids": self.room_ids,
            "room_name": room_display,
            "room_names": self.room_names,
            "params": self.params, "status": self.status,
            "started_at": self.started_at, "stopped_at": self.stopped_at,
            "elapsed_seconds": self.elapsed(), "gifts_sent": self.gifts_sent,
            "gift_target": self.gift_target, "park_ends_at": self.park_ends_at,
            "play_ends_at": self.play_ends_at,
        }

class MigXBot:
    def __init__(self):
        self.token = None
        self.user = None
        self.user_id = None
        self.username = None
        self.level = None
        self.room_id = None
        self.room_name = None
        self.room_members = None
        self.saldo = None
        self.connected = False
        self.tasks = {}
        self.tasks_lock = threading.Lock()
        self.heartbeat_ativo = False
        self.heartbeats_enviados = 0
        self.mensagens_recebidas = 0
        self._game_state = {}
        self.logs = []
        self.log_lock = threading.Lock()
        self.started_at = None
        self.sio = None
        self.current_room_to_rejoin = None

    def _log(self, nivel, texto):
        entrada = {"ts": datetime.now().strftime("%H:%M:%S"), "nivel": nivel, "texto": str(texto)}
        with self.log_lock:
            self.logs.append(entrada)
            if len(self.logs) > 300:
                self.logs = self.logs[-300:]
        print(f"[{entrada['ts']}] [{nivel}] {texto}")

    def estado(self):
        with self.log_lock:
            logs = list(self.logs)
        with self.tasks_lock:
            tasks = [t.to_dict() for t in self.tasks.values()]
        uptime = int(time.time() - self.started_at) if self.started_at else 0
        return {
            "connected": self.connected, "username": self.username, "level": self.level,
            "room_id": self.room_id, "room_name": self.room_name, "room_members": self.room_members,
            "saldo": self.saldo, "heartbeats_enviados": self.heartbeats_enviados,
            "mensagens_recebidas": self.mensagens_recebidas, "uptime_segundos": uptime,
            "tasks": tasks, "logs": logs[-80:],
        }

    def start(self, username, password):
        self._log("info", "Logging in to MigX...")
        sessao = fazer_login(username, password)
        if not sessao:
            self._log("err", "Invalid credentials.")
            return False, "Invalid credentials (wrong username/password)"

        self.token = sessao["token"]
        self.user = sessao["user"]
        self.user_id = self.user["_id"]
        self.username = self.user["username"]
        self.level = self.user.get("level", 1)
        self.started_at = time.time()
        self._log("ok", f"Login OK: {self.username} (level {self.level})")

        self.sio = socketio.Client(
            reconnection=True, reconnection_attempts=0, reconnection_delay=2,
            reconnection_delay_max=30, randomization_factor=0.5,
            logger=False, engineio_logger=False, request_timeout=30,
        )
        self._registar_eventos()

        device_id = str(uuid.uuid4())
        auth = {"token": f"Bearer {self.token}", "clientVersion": "web", "platform": "web", "deviceId": device_id}
        
        self._log("info", "Connecting to Socket.IO...")
        try:
            self.sio.connect(SOCKET_URL, transports=["polling", "websocket"], auth=auth, wait_timeout=30)
            return True, None
        except Exception as e:
            self._log("err", f"Socket.IO connection failed: {e}")
            return False, f"Socket.IO connection failed: {str(e)}"

    def _registar_eventos(self):
        @self.sio.event
        def connect():
            self.connected = True
            self._log("ok", "Connected to MigX Socket.IO")
            self.heartbeat_ativo = True
            threading.Thread(target=self._loop_heartbeat, daemon=True).start()
            if self.current_room_to_rejoin:
                try:
                    entrar_sala_http(self.token, self.current_room_to_rejoin)
                    self.sio.emit("room:join", {"roomId": self.current_room_to_rejoin})
                except Exception:
                    pass

        @self.sio.event
        def disconnect():
            self.connected = False
            self.heartbeat_ativo = False
            self._log("warn", "Disconnected from Socket.IO — reconnection is automatic")

        @self.sio.on("room:message")
        def on_rm(data): self._on_room_message(data)
        @self.sio.on("roomMessage")
        def on_rm2(data): self._on_room_message(data)

        @self.sio.on("user:balance")
        def on_bal(data):
            self.saldo = data.get("balance")
            self._log("info", f"Balance: {self.saldo}")

        @self.sio.on("game:state")
        def on_gs(data): self._on_game_state(data)
        @self.sio.on("gameState")
        def on_gs2(data): self._on_game_state(data)

        @self.sio.on("roomCountChanged")
        def on_rc(data):
            if data.get("roomId") == self.room_id:
                self.room_members = data.get("count")

        @self.sio.on("*")
        def on_any(event, data): pass

    def _loop_heartbeat(self):
        while self.heartbeat_ativo:
            try:
                if self.sio and self.sio.connected:
                    self.sio.emit("user:heartbeat")
                    self.heartbeats_enviados += 1
            except Exception as e:
                self._log("err", f"Heartbeat error: {e}")
                self.connected = False
            time.sleep(HEARTBEAT_INTERVALO)

    def _ensure_in_room(self, room_id):
        if self.room_id == room_id:
            return True
        try:
            with _room_join_lock:
                entrar_sala_http(self.token, room_id)
                time.sleep(0.3)
            self.sio.emit("room:join", {"roomId": room_id})
            self.current_room_to_rejoin = room_id
            self.room_id = room_id
            room = find_room(room_id)
            self.room_name = room['name'] if room else f"Room {room_id[:6]}..."
            self._log("ok", f"Joined '{self.room_name}'")
            return True
        except Exception as e:
            self._log("err", f"Failed to join room {room_id}: {e}")
            return False

    def create_task(self, action, room_ids, params):
        if not room_ids: return None, "No rooms selected"
        room_names = []
        for rid in room_ids:
            room = find_room(rid)
            if not room:
                return None, f"Unknown room {rid}. Ask admin to add it."
            if action not in room["actions"]:
                return None, f"Action '{action}' not allowed in '{room['name']}'"
            room_names.append(room["name"])

        task = Task(action, room_ids, room_names, params)
        if action == "park" and params.get("park_duration_min", 0) > 0:
            task.park_ends_at = time.time() + params["park_duration_min"] * 60
        if is_event_driven(action) and params.get("play_duration_min", 0) > 0:
            task.play_ends_at = time.time() + params["play_duration_min"] * 60

        with self.tasks_lock:
            self.tasks[task.id] = task

        self._log("ok", f"Task {task.id} created: {action} in {len(room_ids)} room(s)")
        threading.Thread(target=self._run_task, args=(task,), daemon=True).start()
        return task, None

    def stop_task(self, task_id):
        with self.tasks_lock:
            task = self.tasks.get(task_id)
            if not task: return False, "Task not found"
            if task.status != "running": return False, "Task already finished"
            task.stop_event.set()
            task.status = "stopped"
            task.stopped_at = time.time()
        self._log("warn", f"Task {task_id} stopped by user")
        return True, None

    def _run_task(self, task):
        try:
            if task.action == "park":
                self._run_park(task)
            elif task.action == "gift":
                self._run_gift(task)
            elif is_event_driven(task.action):
                self._run_game(task)
        except Exception as e:
            self._log("err", f"Task {task.id} crashed: {e}")
        finally:
            if task.status == "running":
                task.status = "finished"
                task.stopped_at = time.time()
            self._log("info", f"Task {task.id} ended ({task.status})")

    def _run_park(self, task):
        duration = task.params.get("park_duration_min", 0)
        room_id = task.room_ids[0]
        keepalive_cmd = "!start"
        
        self._log("info", f"[park] Staying in '{task.room_names[0]}' (keep-alive: {keepalive_cmd})")
        
        while not task.stop_event.is_set():
            if duration > 0 and task.park_ends_at and time.time() >= task.park_ends_at:
                self._log("info", f"[park] Duration reached")
                return
            
            if self.room_id != room_id:
                try:
                    with _room_join_lock:
                        entrar_sala_http(self.token, room_id)
                        time.sleep(0.5)
                    self.sio.emit("room:join", {"roomId": room_id})
                    self.current_room_to_rejoin = room_id
                    self.room_id = room_id
                    self.room_name = task.room_names[0]
                    self._log("ok", f"[park] {self.username} → '{self.room_name}'")
                except Exception as e:
                    self._log("err", f"[park] failed: {e}")
            
            try:
                ok, resp = enviar_mensagem_http(self.token, room_id, keepalive_cmd)
                if ok:
                    self._log("info", f"[park] keep-alive accepted")
                else:
                    self._log("info", f"[park] keep-alive sent (rejected by server)")
            except Exception as e:
                self._log("err", f"[park] keep-alive error: {e}")
            
            for _ in range(45):
                if task.stop_event.is_set(): return
                time.sleep(1)

    def _run_gift(self, task):
        room_id = task.room_ids[0]
        gift_key = get_gift_key(task.params.get("gift_key", "fly"))
        count = task.params.get("gift_count", 1)
        interval = max(1.0, task.params.get("gift_interval", 1))
        
        self._log("info", f"[gift] {task.room_names[0]}: sending {count}x '{gift_key}' every {interval}s")
        self._ensure_in_room(room_id)
        
        sent = 0
        attempts = 0
        max_attempts = count * 10
        
        while sent < count and not task.stop_event.is_set() and attempts < max_attempts:
            attempts += 1
            ok, resp = enviar_mensagem_http(self.token, room_id, f"/gift all {gift_key}")
            if ok:
                sent += 1
                task.gifts_sent = sent
                self._log("ok", f"[gift] {task.room_names[0]}: {sent}/{count} sent")
                task.stop_event.wait(interval)
            else:
                err = str(resp)[:120]
                self._log("warn", f"[gift] rejected: {err}")
                match = re.search(r'Wait (\d+) second', err)
                if match:
                    wait_s = int(match.group(1))
                    self._log("info", f"[gift] waiting {wait_s}s")
                    task.stop_event.wait(wait_s + 1)
                else:
                    task.stop_event.wait(2)
        
        self._log("info", f"[gift] Finished. {sent}/{count} sent.")

    def _run_game(self, task):
        room_id = task.room_ids[0]
        self._ensure_in_room(room_id)
        while not task.stop_event.is_set():
            if task.play_ends_at and time.time() >= task.play_ends_at:
                self._log("info", f"[{task.action}] duration reached")
                break
            task.stop_event.wait(1)

    def _on_room_message(self, data):
        self.mensagens_recebidas += 1
        msg = data.get("message", {}) or {}
        content = msg.get("content", "")
        sender = msg.get("sender", {}) or {}
        mtype = msg.get("messageType", "")
        if mtype == "system":
            clean = re.sub(r"<[^>]+>", "", content)[:140]
            self._log("info", f"[system] {clean}")
        else:
            self._log("info", f"{sender.get('username', '?')}: {content[:140]}")

    def _on_game_state(self, data):
        if not isinstance(data, dict): return
        game = data.get("game") or data
        name = game.get("gameName")
        if not name: return
        with self.tasks_lock:
            active = [t for t in self.tasks.values() if t.status == "running"]
        if name == "royalebattle":
            for t in active:
                if t.action == "royale": self._handle_royale(t, game)
        elif name == "cricket":
            for t in active:
                if t.action == "cricket": self._handle_cricket(t, game)
        elif name == "dice":
            for t in active:
                if t.action == "dice": self._handle_dice(t, game)
        elif name in ("lowcard", "cards", "cardbattle", "card", None):
            for t in active:
                if t.action == "lowcard": self._handle_lowcard(t, game)

    def _handle_royale(self, task, game):
        key = f"royale-{task.id}"
        phase = game.get("phase")
        started_at = game.get("startedAt")
        state = self._game_state.setdefault(key, {"last_started": None, "bet": False})
        if started_at != state["last_started"]:
            state["last_started"] = started_at
            state["bet"] = False
            self._log("info", f"[royale] {task.room_names[0]}: new round")
        if phase == "bidding" and not state["bet"]:
            state["bet"] = True
            threading.Thread(target=self._royale_bet, args=(task,), daemon=True).start()

    def _royale_bet(self, task):
        time.sleep(random.uniform(3, 6))
        if task.stop_event.is_set(): return
        room_id = task.room_ids[0]
        enviar_mensagem_http(self.token, room_id, "!bet red 100")
        task.gifts_sent += 1
        time.sleep(random.uniform(2, 4))
        if task.stop_event.is_set(): return
        enviar_mensagem_http(self.token, room_id, "!bet blue 100")
        task.gifts_sent += 1

    def _handle_cricket(self, task, game):
        key = f"cricket-{task.id}"
        game_id = game.get("gameId")
        phase = game.get("phase")
        players = game.get("players", []) or []
        join_credit = game.get("joinCredit", 0) or 0
        action_cmd = game.get("actionCommand") or "!d"
        state = self._game_state.setdefault(key, {"game_id": None, "entered": False, "played": False, "join_credit": 0, "action_cmd": "!d"})
        if game_id != state["game_id"]:
            state.update({"game_id": game_id, "entered": False, "played": False, "join_credit": join_credit, "action_cmd": action_cmd})
            self._log("info", f"[cricket] {task.room_names[0]}: new game {game_id} join={join_credit}")
        if join_credit > 100: return
        meu = next((p for p in players if p.get("userId") == self.user_id), None)
        estou = meu is not None
        if phase == "joining" and not estou and not state["entered"]:
            state["entered"] = True
            threading.Thread(target=self._cricket_enter, args=(task,), daemon=True).start()
        elif phase in ("rolling", "playing", "acting", "choosing", "drawing", "bidding"):
            if meu and meu.get("canAct") and not meu.get("acted") and not meu.get("isOut"):
                if not state["played"]:
                    state["played"] = True
                    threading.Thread(target=self._cricket_play, args=(task, state["action_cmd"]), daemon=True).start()

    def _cricket_enter(self, task):
        time.sleep(random.uniform(1, 2))
        if task.stop_event.is_set(): return
        enviar_mensagem_http(self.token, task.room_ids[0], "!j")

    def _cricket_play(self, task, cmd):
        time.sleep(random.uniform(3, 8))
        if task.stop_event.is_set(): return
        if random.random() > 0.7: return
        enviar_mensagem_http(self.token, task.room_ids[0], cmd or "!d")
        task.gifts_sent += 1

    def _handle_dice(self, task, game):
        key = f"dice-{task.id}"
        game_id = game.get("gameId")
        phase = game.get("phase")
        players = game.get("players", []) or []
        join_credit = game.get("joinCredit", 0) or 0
        state = self._game_state.setdefault(key, {"game_id": None, "entered": False, "rolled": False, "join_credit": 0})
        if game_id != state["game_id"]:
            state.update({"game_id": game_id, "entered": False, "rolled": False, "join_credit": join_credit})
            self._log("info", f"[dice] {task.room_names[0]}: new game {game_id} join={join_credit}")
        if join_credit > 100: return
        meu = next((p for p in players if p.get("userId") == self.user_id), None)
        estou = meu is not None
        if phase == "joining" and not estou and not state["entered"]:
            state["entered"] = True
            threading.Thread(target=self._dice_enter, args=(task,), daemon=True).start()
        elif phase in ("rolling", "playing", "acting"):
            if meu and meu.get("canAct") and not meu.get("acted") and not state["rolled"]:
                state["rolled"] = True
                threading.Thread(target=self._dice_roll, args=(task,), daemon=True).start()

    def _dice_enter(self, task):
        time.sleep(random.uniform(1, 2))
        if task.stop_event.is_set(): return
        enviar_mensagem_http(self.token, task.room_ids[0], "!j")

    def _dice_roll(self, task):
        time.sleep(random.uniform(3, 8))
        if task.stop_event.is_set(): return
        if random.random() > 0.7: return
        enviar_mensagem_http(self.token, task.room_ids[0], "!r")
        task.gifts_sent += 1

    def _handle_lowcard(self, task, game):
        key = f"lowcard-{task.id}"
        game_id = game.get("gameId")
        phase = game.get("phase")
        players = game.get("players", []) or []
        join_credit = game.get("joinCredit", 0) or 0
        
        state = self._game_state.setdefault(key, {
            "game_id": None, "entered": False, "started": False, "played": False,
        })
        
        if game_id != state["game_id"]:
            state.update({"game_id": game_id, "entered": False, "started": False, "played": False})
            self._log("info", f"[lowcard] {task.room_names[0]}: new game {game_id} join={join_credit}")
        
        if join_credit > 100:
            return
        
        meu = next((p for p in players if p.get("userId") == self.user_id), None)
        estou = meu is not None
        
        # 1) Entrar no jogo
        if phase == "joining" and not estou and not state["entered"]:
            state["entered"] = True
            threading.Thread(target=self._lowcard_enter, args=(task,), daemon=True).start()
        
        # 2) Dar start assim que estiver dentro (NÃO usar !bet)
        elif estou and phase in ("joining", "bidding") and not state["started"]:
            state["started"] = True
            threading.Thread(target=self._lowcard_start, args=(task,), daemon=True).start()
        
        # 3) Jogar quando for a vez
        elif phase in ("rolling", "playing", "acting", "choosing", "drawing", "bidding"):
            if meu and meu.get("canAct") and not meu.get("acted") and not meu.get("isOut"):
                if not state["played"]:
                    state["played"] = True
                    threading.Thread(target=self._lowcard_play, args=(task,), daemon=True).start()
        
        if meu and meu.get("acted"):
            state["played"] = True

    def _lowcard_enter(self, task):
        time.sleep(random.uniform(1, 2))
        if task.stop_event.is_set():
            return
        enviar_mensagem_http(self.token, task.room_ids[0], "!j")
        self._log("ok", f"[lowcard] entrou (!j)")

    def _lowcard_start(self, task):
        """Start com valor entre 5.000 e 10.000. NÃO usar !bet."""
        time.sleep(random.uniform(2, 4))
        if task.stop_event.is_set():
            return
        room_id = task.room_ids[0]
        valor = random.randint(5000, 10000)
        ok, resp = enviar_mensagem_http(self.token, room_id, f"!start {valor}")
        if ok:
            task.gifts_sent += 1
            self._log("ok", f"[lowcard] start {valor}")
        else:
            time.sleep(2)
            if task.stop_event.is_set():
                return
            ok2, resp2 = enviar_mensagem_http(self.token, room_id, f"!start {valor}")
            if ok2:
                task.gifts_sent += 1
                self._log("ok", f"[lowcard] start {valor} (retry)")
            else:
                self._log("warn", f"[lowcard] start falhou: {str(resp2)[:120]}")

    def _lowcard_play(self, task):
        time.sleep(random.uniform(3, 8))
        if task.stop_event.is_set():
            return
        if random.random() > 0.7:
            return
        enviar_mensagem_http(self.token, task.room_ids[0], "!d")
        task.gifts_sent += 1

    def stop(self):
        with self.tasks_lock:
            for t in self.tasks.values():
                if t.status == "running":
                    t.stop_event.set()
                    t.status = "stopped"
                    t.stopped_at = time.time()
        self.heartbeat_ativo = False
        self._log("warn", "Stopping bot...")
        if self.sio and self.sio.connected:
            try: self.sio.disconnect()
            except Exception: pass