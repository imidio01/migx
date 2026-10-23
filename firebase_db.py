# firebase_db.py
import os
import json
import time
import firebase_admin
from firebase_admin import credentials, firestore

cred_json = os.getenv("FIREBASE_CREDENTIALS")

if cred_json:
    cred_dict = json.loads(cred_json)
    cred = credentials.Certificate(cred_dict)
    firebase_admin.initialize_app(cred)
else:
    try:
        cred = credentials.Certificate("serviceAccount.json")
        firebase_admin.initialize_app(cred)
    except Exception as e:
        print(f"[FIREBASE] Aviso: Não foi possível inicializar. {e}")

try:
    db = firestore.client()
except Exception:
    db = None

# ---------- BOTS ----------
def save_bot(main_user, bot_username, bot_password):
    if not db: return
    doc_ref = db.collection("bot_groups").document(main_user).collection("bots").document(bot_username)
    doc_ref.set({
        "username": bot_username,
        "password": bot_password,
        "added_at": firestore.SERVER_TIMESTAMP
    })

def get_bots(main_user):
    if not db: return []
    bots_ref = db.collection("bot_groups").document(main_user).collection("bots")
    return [doc.to_dict() | {"username": doc.id} for doc in bots_ref.stream()]

def delete_bot(main_user, bot_username):
    if not db: return
    db.collection("bot_groups").document(main_user).collection("bots").document(bot_username).delete()

def get_all_main_users():
    if not db: return []
    return [doc.id for doc in db.collection("bot_groups").stream()]

# ---------- TAREFAS ----------
def save_task(main_user, bot_username, task_dict):
    if not db: return
    doc_ref = (db.collection("bot_groups").document(main_user)
               .collection("bots").document(bot_username)
               .collection("tasks").document(task_dict["id"]))
    doc_ref.set({
        "action": task_dict.get("action"),
        "room_ids": task_dict.get("room_ids", []),
        "room_name": task_dict.get("room_name", ""),
        "params": task_dict.get("params", {}),
        "status": "running",
        "started_at": task_dict.get("started_at", time.time()),
    })

def delete_task(main_user, bot_username, task_id):
    if not db: return
    (db.collection("bot_groups").document(main_user)
     .collection("bots").document(bot_username)
     .collection("tasks").document(task_id).delete())

def get_tasks(main_user, bot_username):
    if not db: return []
    tasks_ref = (db.collection("bot_groups").document(main_user)
                 .collection("bots").document(bot_username)
                 .collection("tasks"))
    return [doc.to_dict() | {"id": doc.id} for doc in tasks_ref.stream()]

# ---------- SALAS ----------
def get_rooms():
    if not db: return []
    return [doc.to_dict() | {"id": doc.id} for doc in db.collection("rooms").stream()]

def save_room(room_id, name, actions):
    if not db: return
    db.collection("rooms").document(room_id).set({
        "name": name,
        "actions": actions,
        "updated_at": firestore.SERVER_TIMESTAMP
    })

def delete_room(room_id):
    if not db: return
    db.collection("rooms").document(room_id).delete()

def seed_rooms_if_empty(default_rooms):
    if not db: return
    if not list(db.collection("rooms").limit(1).stream()):
        print("[FIREBASE] A criar salas padrão no Firestore...")
        for r in default_rooms:
            save_room(r["id"], r["name"], r["actions"])