# -*- coding: utf-8 -*-
"""
emailer.py —— 本地/手动发信(Gmail SMTP)。CI 里改用 dawidd6/action-send-mail。

读取环境变量(与 GitHub secrets 同名):
    MAIL_USERNAME  你的 Gmail 地址
    MAIL_PASSWORD  16 位应用专用密码(不是登录密码)
    MAIL_TO        收件人(缺省=发给自己)

    python emailer.py --session close --date 2026-06-24
"""
from __future__ import annotations
import argparse
import json
import logging
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

import config

log = logging.getLogger("emailer")


def send_mail(subject: str, body: str, attachments: list[Path], to: str | None = None):
    user, pwd = config.MAIL_USERNAME, config.MAIL_PASSWORD
    to = to or config.MAIL_TO or user
    if not user or not pwd:
        raise RuntimeError("未配置 MAIL_USERNAME / MAIL_PASSWORD 环境变量")

    msg = EmailMessage()
    msg["From"] = f"A股资金流监控 <{user}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    for path in attachments:
        path = Path(path)
        data = path.read_bytes()
        msg.add_attachment(data, maintype="video", subtype="mp4", filename=path.name)

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=ctx) as s:
        s.login(user, pwd)
        s.send_message(msg)
    log.info("已发送邮件 → %s (%d 个附件)", to, len(attachments))


def main():
    ap = argparse.ArgumentParser(description="发送已渲染的桑基图视频")
    ap.add_argument("--session", required=True, choices=["midday", "close"])
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stdout)

    meta_path = config.OUT_DIR / f"{args.session}_{args.date}.meta.json"
    if not meta_path.exists():
        log.error("找不到 %s,请先 run_window 渲染。", meta_path)
        sys.exit(2)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    out = Path(meta["out"])

    subject = f"📊 {meta['date_label']} {meta['session_label']}资金流向桑基图"
    body = (f"{meta['date_label']} {meta['session_label']}板块资金流向,见附件 {out.name}。\n\n"
            f"流入Top: {' '.join(meta['inflow_top'])}\n"
            f"流出Top: {' '.join(meta['outflow_top'])}\n\n"
            f"{config.DISCLAIMER}")
    send_mail(subject, body, [out])


if __name__ == "__main__":
    main()
