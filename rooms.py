# rooms.py
ROOMS = [
    # ===== CHAT / GIFT / PARK =====
    {"id": "6aae359b75e87cd005be0117", "name": "Mozambique", "actions": ["park", "gift"], "category": "chat"},
    {"id": "6aa7990fafd5401b0492fbdf", "name": "Happy Dashain", "actions": ["park", "gift"], "category": "chat"},
    {"id": "6a6562cf38aaa64fd0450807", "name": "Dark World", "actions": ["park", "gift"], "category": "chat"},
    {"id": "6a5723f296fc0ea2f727360b", "name": "Nepal", "actions": ["park", "gift"], "category": "chat"},
    {"id": "6a5724ca96fc0ea2f7273d49", "name": "India", "actions": ["park", "gift"], "category": "chat"},
    {"id": "6a5725f996fc0ea2f7274583", "name": "Indonesia", "actions": ["park", "gift"], "category": "chat"},
    {"id": "6a6b23dbfcb779dc7dd2b5e4", "name": "Migx India", "actions": ["park", "gift"], "category": "chat"},
    {"id": "6a75a6eab24bf3ee88260e87", "name": "Migx Juction", "actions": ["park", "gift"], "category": "chat"},
    {"id": "6a591f117a8c807a7c1cb8fc", "name": "Maldives", "actions": ["park", "gift"], "category": "chat"},
    {"id": "6a5725c496fc0ea2f7274320", "name": "Bangladesh", "actions": ["park", "gift"], "category": "chat"},
    {"id": "6a57236196fc0ea2f7273026", "name": "MigxCareline", "actions": ["park", "gift"], "category": "chat"},
    {"id": "6ac47e17c5b4fdaa2078b5b1", "name": "Migx Daily Contest", "actions": ["park", "gift"], "category": "chat"},
    {"id": "6ab5d01202313b12b949cb1a", "name": "Putos da Pesada", "actions": ["park", "gift"], "category": "chat"},

    # ===== GAMES: ROYALE =====
    {"id": "6a5c48110cf9038123569837", "name": "Royale Battle", "actions": ["royale"], "category": "game"},

    # ===== GAMES: CRICKET =====
    {"id": "6a5723c496fc0ea2f7273438", "name": "Cricket 1", "actions": ["cricket"], "category": "game"},
    {"id": "6a756b9da0e8c996a47e1a20", "name": "Cricket 2", "actions": ["cricket"], "category": "game"},
    {"id": "6a756bd3f670625c9b9a8a9a", "name": "Cricket 3", "actions": ["cricket"], "category": "game"},
    {"id": "6a756bbef670625c9b9a8689", "name": "Cricket 4", "actions": ["cricket"], "category": "game"},
    {"id": "6a756b58a0e8c996a47e1174", "name": "Cricket 5", "actions": ["cricket"], "category": "game"},

    # ===== GAMES: DICE =====
    {"id": "6a57253f96fc0ea2f727402a", "name": "Dice 1", "actions": ["dice"], "category": "game"},
    {"id": "6a6369ac38aaa64fd0c94b7b", "name": "Dice 2", "actions": ["dice"], "category": "game"},
    {"id": "6a756d95a0e8c996a47e5aa4", "name": "Dice 3", "actions": ["dice"], "category": "game"},
    {"id": "6a756e19f670625c9b9ae5a3", "name": "Dice 4", "actions": ["dice"], "category": "game"},

    # ===== GAMES: LOWCARD =====
    {"id": "6a57239e96fc0ea2f7273311", "name": "Lowcard 1", "actions": ["lowcard"], "category": "game"},
    {"id": "6a72e761bf06e8f839c63918", "name": "Lowcard 2", "actions": ["lowcard"], "category": "game"},
    {"id": "6a72e7eabf06e8f839c65c9c", "name": "Lowcard 3", "actions": ["lowcard"], "category": "game"},
    {"id": "6a756ac607c537cc5ef4cb3a", "name": "Lowcard 4", "actions": ["lowcard"], "category": "game"},
]

ACTIONS = [
    {"key": "park",    "label": "Park (stay idle in room)"},
    {"key": "gift",    "label": "Send Gifts"},
    {"key": "royale",  "label": "Play Royale Battle"},
    {"key": "cricket", "label": "Play Cricket"},
    {"key": "dice",    "label": "Play Dice"},
    {"key": "lowcard", "label": "Play Lowcard"},
]

def find_room(room_id):
    for r in ROOMS:
        if r["id"] == room_id:
            return r
    return None

def rooms_for_action(action):
    return [r for r in ROOMS if action in r["actions"]]

def is_event_driven(action):
    return action in ("royale", "cricket", "dice", "lowcard")