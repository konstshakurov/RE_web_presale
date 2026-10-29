"""Отправка письма по SMTP для алертов и отчётов (KON-49). Только стандартная библиотека.

Использование: send_mail.py --subject "Тема" [--to a@b,c@d] [--attach файл.md] < тело_письма
Настройки - из окружения (ops.env):
  SMTP_HOST      пусто = почта не настроена: пишет в журнал "mail_skipped" и выходит с кодом 0
  SMTP_PORT      587 (STARTTLS) или 465 (SSL) или 25 (внутренний relay без TLS); по умолчанию 587
  SMTP_SECURITY  starttls | ssl | none. По умолчанию: none для порта 25, ssl для 465, иначе starttls
  SMTP_STARTTLS  1 - принудительно STARTTLS (для порта 25 по умолчанию выключен); 0 - без STARTTLS
  SMTP_USER      логин (пусто = без авторизации: login() не вызывается)
  SMTP_PASSWORD  пароль, либо SMTP_PASSWORD_FILE - путь к файлу с паролем (права 600)
  SMTP_FROM      адрес отправителя (по умолчанию SMTP_USER)
  SMTP_VERIFY_TLS 1 (по умолчанию) | 0 - не проверять сертификат внутреннего сервера
  ALERT_EMAIL_TO получатель(и) через запятую (если не задан --to)
Пароль и тело письма в журнал не пишутся. Код выхода: 0 - отправлено или почта не настроена,
1 - ошибка доставки.
"""
import argparse
import json
import os
import smtplib
import ssl
import sys
from datetime import datetime
from email.message import EmailMessage


def log(level, event, msg=""):
    print(json.dumps({"ts": datetime.now().astimezone().isoformat(timespec="seconds"), "level": level,
                      "component": "mail", "event": event, "msg": msg}, ensure_ascii=False), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--to", default="")
    ap.add_argument("--attach", default="")
    a = ap.parse_args()
    env = os.environ.get
    host = env("SMTP_HOST", "").strip()
    to = [x.strip() for x in (a.to or env("ALERT_EMAIL_TO", "")).split(",") if x.strip()]
    if not host:
        log("info", "mail_skipped", "SMTP_HOST не задан, письмо не отправлено")
        return 0
    if not to:
        log("warn", "mail_skipped", "получатель не задан (ALERT_EMAIL_TO)")
        return 0
    sec_env = env("SMTP_SECURITY", "").strip().lower()
    port = int(env("SMTP_PORT", "") or (465 if sec_env == "ssl" else 587))
    if sec_env:
        security = sec_env
    elif port == 465:
        security = "ssl"
    elif port == 25:
        security = "none"
    else:
        security = "starttls"
    st = env("SMTP_STARTTLS", "").strip()
    if st == "1" and security == "none":
        security = "starttls"
    elif st == "0" and security == "starttls":
        security = "none"
    user = env("SMTP_USER", "")
    password = env("SMTP_PASSWORD", "")
    pwf = env("SMTP_PASSWORD_FILE", "")
    try:
        if not password and pwf:
            with open(pwf, encoding="utf-8") as f:
                password = f.read().strip()
        body = sys.stdin.buffer.read().decode("utf-8", "replace") if not sys.stdin.isatty() else ""
        msg = EmailMessage()
        msg["Subject"] = " ".join(a.subject.split())[:100]
        msg["From"] = env("SMTP_FROM", "") or user or "re_web_presale@localhost"
        msg["To"] = ", ".join(to)
        msg.set_content(body or a.subject, cte="base64")
        if a.attach:
            with open(a.attach, "rb") as f:
                msg.add_attachment(f.read(), maintype="text", subtype="markdown",
                                   filename=os.path.basename(a.attach))
        ctx = ssl.create_default_context()
        if env("SMTP_VERIFY_TLS", "1") == "0":
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        timeout = int(env("SMTP_TIMEOUT", "") or 20)
        if security == "ssl":
            srv = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx)
        else:
            srv = smtplib.SMTP(host, port, timeout=timeout)
        with srv:
            srv.ehlo()
            if security == "starttls":
                srv.starttls(context=ctx)
                srv.ehlo()
            if user:
                srv.login(user, password)
            srv.send_message(msg)
        log("info", "mail_sent", f"получателей: {len(to)}")
        return 0
    except Exception as e:  # текст ошибки без пароля: smtplib его не включает
        log("error", "mail_failed", f"{type(e).__name__}: {str(e)[:200]}")
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
