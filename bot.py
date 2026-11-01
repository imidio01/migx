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

_LETTER_MAP = {
    "a": 1, "j": 11, "q": 12, "k": 13, "10": 10,
}

# --- Perfis de agressividade por jogo ---
# play_prob: prob. base de jogar quando é "oportuno"
# min_gap_between_plays: segundos mínimos entre jogadas
# reaction_min/max: delay antes de mandar o comando
# skip_after_loss_rounds: rondas a ignorar depois de uma derrota
_GAME_PROFILES = {
    "cricket": {
        "play_prob": 0.08,
        "min_gap_between_plays": 45,
        "reaction_min": 2.5,
        "reaction_max": 6.0,
        "skip_after_loss_rounds": 3,
    },
    "dice": {
        "play_prob": 0.10,
        "min_gap_between_plays": 40,
        "reaction_min": 2.0,
        "reaction_max": 5.5,
        "skip_after_loss_rounds": 3,
    },
    "lowcard": {
        "play_prob": 0.18,
        "min_gap_between_plays": 25,
        "reaction_min": 1.5,
        "reaction_max": 4.0,
        "skip_after_loss_rounds": 2,
    },
    "royale": {
        "play_prob": 0.25,
        "min_gap_between_plays": 20,
        "reaction_min": 1.2,
        "reaction_max": 3.5,
        "skip_after_loss_rounds": 1,
    },
}


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
        self.logs = []
        self.log_lock = threading.Lock()
        self.started_at = None
        self.sio = None
        self.current_room_to_rejoin = None
        self._game_states = {}

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
        def on_rm(data):
            self._on_room_message(data)

        @self.sio.on("roomMessage")
        def on_rm2(data):
            self._on_room_message(data)

        @self.sio.on("user:balance")
        def on_bal(data):
            self.saldo = data.get("balance")
            self._log("info", f"Balance: {self.saldo}")

        @self.sio.on("roomCountChanged")
        def on_rc(data):
            if data.get("roomId") == self.room_id:
                self.room_members = data.get("count")

        @self.sio.on("*")
        def on_any(event, data):
            pass

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
        if not room_ids:
            return None, "No rooms selected"
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
        if action in ("royale", "cricket", "dice", "lowcard") and params.get("play_duration_min", 0) > 0:
            task.play_ends_at = time.time() + params["play_duration_min"] * 60

        with self.tasks_lock:
            self.tasks[task.id] = task

        self._log("ok", f"Task {task.id} created: {action} in {len(room_ids)} room(s)")
        threading.Thread(target=self._run_task, args=(task,), daemon=True).start()
        return task, None

    def stop_task(self, task_id):
        with self.tasks_lock:
            task = self.tasks.get(task_id)
            if not task:
                return False, "Task not found"
            if task.status != "running":
                return False, "Task already finished"
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
            elif task.action in ("royale", "cricket", "dice", "lowcard"):
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
                if task.stop_event.is_set():
                    return
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

    def _parse_roll_value(self, text):
        """Extrai o valor tirado de uma mensagem do sistema."""
        t = text.strip()
        # Tenta letras primeiro (A, K, Q, J, 10)
        m = re.search(r'\b(A|K|Q|J|10)\b', t, re.I)
        if m:
            v = m.group(1).lower()
            return _LETTER_MAP.get(v)
        # Tenta número 1-13
        m = re.search(r'\b(1[0-3]|[1-9])\b', t)
        if m:
            return int(m.group(1))
        return None

    def _run_game(self, task):
        """Loop conservador: entra no jogo, mas joga raramente e de forma irregular."""
        room_id = task.room_ids[0]
        self._ensure_in_room(room_id)

        profile = _GAME_PROFILES.get(task.action, _GAME_PROFILES["dice"])

        join_cmd = "!j"
        play_cmd = {
            "dice": "!r",
            "cricket": "!d",
            "lowcard": "!d",
            "royale": "!bet red 100",
        }.get(task.action, "!d")

        self._log("info", f"[{task.action}] {task.room_names[0]}: perfil conservador "
                         f"(play_prob={profile['play_prob']}, gap>={profile['min_gap_between_plays']}s)")

        state_key = f"game-{task.id}"
        game_state = {
            "last_value": None,
            "last_value_ts": 0,
            "last_value_user": None,
            "my_turn_at": None,
            "joined": False,
            "last_play_ts": 0,
            "loss_streak": 0,
            "rounds_since_play": 0,
        }
        self._game_states[state_key] = game_state

        last_join = 0
        next_decision = 0
        JOIN_INTERVAL = 25

        try:
            while not task.stop_event.is_set():
                if task.play_ends_at and time.time() >= task.play_ends_at:
                    self._log("info", f"[{task.action}] duration reached")
                    break

                now = time.time()

                # 1) Entrar no jogo (!j) — só se ainda não está dentro
                if now - last_join >= JOIN_INTERVAL and not game_state["joined"]:
                    last_join = now
                    try:
                        ok, resp = enviar_mensagem_http(self.token, room_id, join_cmd)
                        if ok:
                            self._log("info", f"[{task.action}] !j enviado")
                        else:
                            self._log("warn", f"[{task.action}] !j rejeitado: {str(resp)[:100]}")
                    except Exception as e:
                        self._log("err", f"[{task.action}] erro !j: {e}")

                # 2) Decidir se joga — com proteções fortes
                if now >= next_decision:
                    decision = self._decide_play_conservative(game_state, profile, now)
                    if decision == "play":
                        reaction = random.uniform(profile["reaction_min"], profile["reaction_max"])
                        reaction += random.uniform(0, 1.5)
                        task.stop_event.wait(reaction)
                        if task.stop_event.is_set():
                            break

                        try:
                            ok, resp = enviar_mensagem_http(self.token, room_id, play_cmd)
                            if ok:
                                task.gifts_sent += 1
                                game_state["last_play_ts"] = time.time()
                                game_state["rounds_since_play"] = 0
                                self._log("ok", f"[{task.action}] jogada #{task.gifts_sent} "
                                                 f"(após {reaction:.1f}s)")
                            else:
                                self._log("warn", f"[{task.action}] jogada rejeitada: {str(resp)[:100]}")
                        except Exception as e:
                            self._log("err", f"[{task.action}] erro jogada: {e}")

                        next_decision = time.time() + profile["min_gap_between_plays"] \
                                        + random.uniform(0, profile["min_gap_between_plays"] * 0.5)

                    elif decision == "skip":
                        game_state["rounds_since_play"] += 1
                        next_decision = now + random.uniform(4, 12)
                        self._log("info", f"[{task.action}] skip (última: {game_state['last_value']}, "
                                          f"rounds_sem_jogar={game_state['rounds_since_play']})")
                    else:  # wait
                        next_decision = now + random.uniform(2.0, 5.0)

                task.stop_event.wait(0.8)
        finally:
            self._game_states.pop(state_key, None)

        self._log("info", f"[{task.action}] terminou ({task.gifts_sent} jogadas)")

    def _decide_play_conservative(self, gs, profile, now):
        """
        Decide se deve jogar — MUITO mais conservador.
        Retorna: 'play', 'wait' ou 'skip'
        """
        # 0) Respeitar gap mínimo entre jogadas
        since_last = now - gs.get("last_play_ts", 0)
        if since_last < profile["min_gap_between_plays"]:
            return "wait"

        # 1) Se estamos num "loss streak" obrigatório, skip
        if gs["loss_streak"] > 0:
            gs["loss_streak"] -= 1
            return "skip"

        last_val = gs["last_value"]
        last_ts = gs["last_value_ts"]

        # 2) Sem info -> probabilidade baixíssima de jogar
        if last_val is None:
            return "play" if random.random() < profile["play_prob"] * 0.5 else "wait"

        age = now - last_ts
        if age < 2.0:
            return "wait"

        # 3) Aplicar probabilidade base do perfil
        if random.random() > profile["play_prob"]:
            # mesmo quando "decide jogar", 60% das vezes ainda espera
            if random.random() < 0.60:
                return "wait"
            return "skip"

        # 4) Mesmo dentro dos 8-25% de "quer jogar", filtrar por valor observado
        high = last_val >= 9
        low = last_val <= 5

        if high:
            r = random.random()
            if r < 0.85:
                gs["loss_streak"] = profile["skip_after_loss_rounds"]
                return "skip"
            elif r < 0.97:
                return "wait"
            else:
                return "play" if random.random() < 0.5 else "skip"

        if low:
            r = random.random()
            if r < 0.55:
                return "play"
            elif r < 0.85:
                return "wait"
            else:
                return "skip"

        # neutro -> raramente
        r = random.random()
        if r < 0.30:
            return "play"
        elif r < 0.75:
            return "wait"
        else:
            gs["loss_streak"] = profile["skip_after_loss_rounds"]
            return "skip"

    def _on_room_message(self, data):
        self.mensagens_recebidas += 1
        msg = data.get("message", {}) or {}
        content = msg.get("content", "")
        sender = msg.get("sender", {}) or {}
        sender_name = sender.get("username", "?")
        mtype = msg.get("messageType", "")

        if mtype == "system":
            clean = re.sub(r"<[^>]+>", "", content)[:200]
            self._log("info", f"[system] {clean}")

            # Tentar extrair valor tirado
            val = self._parse_roll_value(clean)
            if val is not None:
                for key, gs in self._game_states.items():
                    gs["last_value"] = val
                    gs["last_value_ts"] = time.time()
                    gs["last_value_user"] = sender_name

            # Detetar "your turn" / "sua vez" / "é a tua vez"
            lower = clean.lower()
            if any(k in lower for k in ("your turn", "sua vez", "é a tua vez", "your move", "tua vez")):
                for key, gs in self._game_states.items():
                    gs["my_turn_at"] = time.time()

            # Detetar entrada no jogo
            if any(k in lower for k in ("joined the game", "entrou no jogo", "entrou na partida")):
                if self.username and self.username.lower() in lower:
                    for key, gs in self._game_states.items():
                        gs["joined"] = True

            # Detetar fim de jogo
            if any(k in lower for k in ("game over", "fim do jogo", "terminou", "winner", "vencedor")):
                for key, gs in self._game_states.items():
                    gs["joined"] = False
                    gs["last_value"] = None
                    gs["last_value_ts"] = 0

            # Detetar derrota -> forçar pausa (loss_streak)
            if any(k in lower for k in ("you lost", "perdeste", "lost the round", "perdeu")):
                for key, gs in self._game_states.items():
                    gs["loss_streak"] = max(gs.get("loss_streak", 0), 3)
                    gs["joined"] = False
                    gs["last_value"] = None
                    gs["last_value_ts"] = 0
        else:
            self._log("info", f"{sender_name}: {content[:140]}")

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
            try:
                self.sio.disconnect()
            except Exception:
                pass