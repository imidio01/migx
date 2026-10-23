# gifts.py
GIFTS = {
    # O fly é o único que confirmaste que funciona. Adiciona os outros à medida que descobres as chaves.
    "fly": {"name": "Fly", "price": 10},
    "happy friday": {"name": "Happy Friday", "price": 13},
    "super car": {"name": "Super Car", "price": 10000},
    "zippo": {"name": "Zippo", "price": 500},
    "ring": {"name": "Ring", "price": 60},
    "muhaah": {"name": "Muhaah", "price": 65},
    "pink diamond": {"name": "Pink Diamond", "price": 750},
    "boxer": {"name": "Boxer", "price": 65},
    "cupid": {"name": "Cupid", "price": 600},
    "happy raksha bandhan": {"name": "Happy Raksha Bandhan", "price": 20},
    "rakhi": {"name": "Rakhi", "price": 500},
    "crown": {"name": "Crown", "price": 15000},
    "champion": {"name": "Champion", "price": 50},
    "everest": {"name": "Everest", "price": 80},
    "lion": {"name": "Lion", "price": 25},
    "super bike": {"name": "Super Bike", "price": 7500},
    "legend": {"name": "Legend", "price": 100},
    "heart": {"name": "Heart", "price": 100},
    "happy birthday": {"name": "Happy Birthday", "price": 30},
    "happy girlfriend day": {"name": "Happy Girlfriend Day", "price": 50},
    "indonesia independence": {"name": "Indonesia Independence", "price": 20},
    "happy nag panchami": {"name": "Happy Nag Panchami", "price": 30},
    "bhairav": {"name": "Bhairav", "price": 20},
    "world teachers day": {"name": "World Teachers Day", "price": 50},
    "tika": {"name": "Tika", "price": 40},
    "dashain aayo": {"name": "Dashain Aayo", "price": 15},
    "you are mine": {"name": "You Are Mine", "price": 40},
    "love brother": {"name": "Love Brother", "price": 40},
    "car": {"name": "Car", "price": 5000},
    "love": {"name": "Love", "price": 100},
    "kiss": {"name": "Kiss", "price": 100},
    "congratulation": {"name": "Congratulation", "price": 50},
    "thank you": {"name": "Thank You", "price": 50},
    "banana": {"name": "Banana", "price": 20},
    "super saturday": {"name": "Super Saturday", "price": 11},
    "land krishna": {"name": "Land Krishna", "price": 50},
    "happy krishna janma": {"name": "Happy Krishna Janma", "price": 30},
    "diwali raji": {"name": "Diwali Raji", "price": 20},
    "happy teacher day": {"name": "Happy Teacher Day", "price": 50},
    "happy fathers day": {"name": "Happy Fathers Day", "price": 30},
    "happy tej": {"name": "Happy Tej", "price": 20},
    "hold my hand": {"name": "Hold My Hand", "price": 50},
    "kohinoor": {"name": "Kohinoor", "price": 1000000},
    "like": {"name": "Like", "price": 40},
    "devil": {"name": "Devil", "price": 500},
    "magnum": {"name": "Magnum", "price": 50},
    "buddha": {"name": "Buddha", "price": 50},
    "happy dashain": {"name": "Happy Dashain", "price": 60},
    "tika janara": {"name": "Tika Janara", "price": 20},
    "shiva ji": {"name": "Shiva Ji", "price": 20},
    "my love": {"name": "My Love", "price": 20},
    "local raksi": {"name": "Local Raksi", "price": 15},
    "machhapuchhire": {"name": "Machhapuchhire", "price": 100},
    "happy ghatasthapana": {"name": "Happy Ghatasthapana", "price": 100},
    "jhapali": {"name": "Jhapali", "price": 60},
    "black diamond": {"name": "Black Diamond", "price": 1000000},
    "ruby heart": {"name": "Ruby Heart", "price": 500},
    "birthday cake": {"name": "Birthday Cake", "price": 60},
    "hug": {"name": "Hug", "price": 40},
}

def get_gift_key(user_input):
    """
    Devolve a chave que o servidor MigX espera.
    O servidor aceita 'fly' (com espaço? não — só 'fly').
    Se o user escrever "Super Car", tenta encontrar na lista.
    Se não encontrar, devolve o input normalizado (minúsculas, espaços → nada? ou _?)
    """
    if not user_input:
        return "fly"
    
    normalized = user_input.strip().lower()
    
    # Se está na lista como chave, devolve a chave tal como está
    if normalized in GIFTS:
        return normalized  # ex: "fly" → "fly", "super car" → "super car" (o servidor pode aceitar com espaço)
    
    # Se o user escreveu o nome (ex: "Super Car"), encontra a chave
    for key, data in GIFTS.items():
        if data["name"].lower() == normalized:
            return key  # devolve a chave como está na lista
    
    # Se não encontrar, devolve o que o user escreveu (sem underscores, o servidor pode não gostar)
    return normalized