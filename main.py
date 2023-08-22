import datetime
import time
import uuid
from email.header import decode_header
from email.utils import parsedate_to_datetime

from fastapi import APIRouter, FastAPI
import imaplib
import email
from typing import Optional

import threading

import uvicorn

from loguru import logger

from fastapi import FastAPI, Request
import chardet
import base64
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

app = FastAPI()


@app.on_event("startup")
def start_ap_scheduler():
    threading.Thread(target=clean_mail_every_thirty_minutes).start()
    scheduler = BackgroundScheduler()
    scheduler.add_job(clean_mail_every_thirty_minutes, trigger=IntervalTrigger(minutes=5))
    scheduler.start()


def clean_mail_every_thirty_minutes():
    try:
        mail = connect_to_mailbox()
        today = datetime.datetime.now()
        two_days_ago = today - datetime.timedelta(minutes=30)

        status, email_ids = mail.search(None, '(All)')
        if status != 'OK':
            # mail.close()
            return

        email_id_list = [x.decode() for x in email_ids[0].split()]

        if email_id_list:
            _, data = mail.fetch(','.join(email_id_list), '(BODY[HEADER.FIELDS (DATE)])')

            new_response_data = chunk_list(data, 2)

            for e_id_index, e_id_data in enumerate(new_response_data):
                e_id = email_id_list[e_id_index]
                raw_header = e_id_data[0][1]
                email_message = email.message_from_string(raw_header.decode('utf-8'))
                date = extract_and_format_date(email_message)
                if datetime.datetime.strptime(date, '%Y-%m-%d %H:%M:%S') < two_days_ago:
                    logger.info("Try to delete e_id_index:{}".format(email_id_list[e_id_index]))
                    mail.store(e_id, '+FLAGS', '\\Deleted')
            mail.expunge()
    except Exception as e:
        logger.exception(e)
    finally:
        try:
            mail.close()
        except Exception as e:
            logger.exception(e)


# threading.Thread(target=clean_mail_every_thirty_minutes).start()

mail_router = APIRouter()

# IMAP Configuration
IMAP_SERVER = 'mail.zzem.cc'
IMAP_PORT = 143
USERNAME = 'mail@zzem.cc'
PASSWORD = 'www123'


def connect_to_mailbox():
    while True:
        try:
            logger.info("正在連線")
            mail = imaplib.IMAP4(IMAP_SERVER, IMAP_PORT)
            mail.login(USERNAME, PASSWORD)
            mail.select('inbox')
            logger.info("成功連線")
            return mail
        except Exception as e:
            logger.exception(e)


def ensure_connected():
    return connect_to_mailbox()


def decode_email_header(header_value):
    if not header_value:
        return ""

    # Decode the email header value
    decoded_headers = decode_header(header_value)
    header_parts = []
    for decoded_string, encoding in decoded_headers:
        if encoding:
            header_parts.append(decoded_string.decode(encoding))
        else:
            header_parts.append(decoded_string if isinstance(decoded_string, str) else decoded_string.decode('utf-8'))
    return '\n'.join(header_parts)


def extract_and_format_date(email_message):
    raw_date = email_message['Date']
    if not raw_date:
        return "1901-01-01 00:00:00"

    # Parse the raw date string to a datetime object
    date_obj = parsedate_to_datetime(raw_date)

    desired_timezone = datetime.timezone.utc
    asia_timezone = datetime.timezone(datetime.timedelta(hours=8))
    date_in_desired_timezone = date_obj.astimezone(asia_timezone)
    # Format the datetime object to the desired string format
    formatted_date = date_in_desired_timezone.strftime('%Y-%m-%d %H:%M:%S')
    return formatted_date


def chunk_list(input_list, chunk_size):
    """Yield successive chunk_size-sized chunks from input_list."""
    for i in range(0, len(input_list), chunk_size):
        yield input_list[i:i + chunk_size]


def fetch_emails(sender: Optional[str] = None, receiver: Optional[str] = None, subject: Optional[str] = None,
                 unread_only: Optional[bool] = False):
    mail = connect_to_mailbox()
    logger.info("成功連線")
    try:
        if unread_only:
            status, email_ids = mail.search(None, '(UNSEEN)')
        else:
            status, email_ids = mail.search(None, 'ALL')
        if status != 'OK':
            raise Exception("Failed to fetch emails")

        # Convert email IDs to list
        email_id_list = [x.decode() for x in email_ids[0].split()]
        if not email_id_list:
            return []

        _, data = mail.fetch(','.join(email_id_list),
                             '(FLAGS BODY[HEADER.FIELDS (MESSAGE-ID FROM TO SUBJECT DATE CONTENT-TRANSFER-ENCODING)] BODY[TEXT])')
        new_response_data = chunk_list(data, 3)
        # Extract email details from response
        emails = []
        for e_id_index, e_id_data in enumerate(new_response_data):
            e_id = email_id_list[e_id_index]
            flags_data = e_id_data[0][0].decode()
            raw_header = e_id_data[0][1]
            email_message = email.message_from_string(raw_header.decode('utf-8'))

            # 检查Content-Transfer-Encoding字段
            content_transfer_encoding = email_message.get('Content-Transfer-Encoding', '').lower()

            message_id = email_message.get('Message-ID', str(uuid.uuid4()))
            from_ = decode_email_header(email_message['From']).replace("\n", "")
            to_ = decode_email_header(email_message['To']).replace("\n", "")
            subject_ = decode_email_header(email_message['Subject']).replace("\n", "")
            date_ = extract_and_format_date(email_message)
            read_status = "\\Seen" in flags_data

            if sender and sender.lower() not in from_.lower():
                continue
            if receiver and receiver.lower() not in to_.lower():
                continue
            if subject and subject.lower() not in subject_.lower():
                continue

            raw_data = e_id_data[1][1]
            if content_transfer_encoding == 'base64':
                # 如果是BASE64编码，则先解码
                raw_data = base64.b64decode(raw_data)
            detected_encoding = chardet.detect(raw_data)['encoding']
            body_data = raw_data.decode(detected_encoding)
            # body_data = e_id_data[1][1].decode('utf-8') if len(
            #     e_id_data) > 1 else ""

            emails.append({
                "id": e_id,
                "message_id": message_id[1:-1],
                "sender": from_,
                "receiver": to_,
                "subject": subject_,
                "date": date_,
                "read": read_status,
                "body": body_data  # Add the body here
            })

        # Sort emails by date
        emails = sorted(emails, key=lambda x: x['date'], reverse=True)
        return emails
    finally:
        mail.close()


def fetch_email_by_message_id(message_id, delete_after_fetch=False):
    mail = connect_to_mailbox()
    try:
        message_id = "<{}>".format(message_id)
        # Use the SEARCH command with HEADER criteria to search for the specific Message-ID
        status, email_ids = mail.search(None, '(HEADER Message-ID "{}")'.format(message_id))

        if status != 'OK' or not email_ids[0]:
            return ""  # Email not found
        email_id = email_ids[0].split()[0]
        _, data = mail.fetch(email_id, '(RFC822)')
        raw_email = data[0][1]
        email_message = email.message_from_bytes(raw_email)
        if delete_after_fetch:
            mail.store(email_id, '+FLAGS', '\\Deleted')
            mail.expunge()
        return fetch_email_content(email_message)
    finally:
        mail.close()


def fetch_email_content(email_message):
    content_charset = email_message.get_content_charset() or 'utf-8'  # Use utf-8 as default if charset is not specified

    if email_message.is_multipart():
        for part in email_message.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get('Content-Disposition'))
            if 'attachment' not in content_disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(content_charset)
    else:
        payload = email_message.get_payload(decode=True)
        if payload:
            return payload.decode(content_charset)
    return ""  # return empty string if no content is found


@app.get("/emails")
async def get_emails(request: Request,
                     sender: Optional[str] = None,
                     receiver: Optional[str] = None,
                     subject: Optional[str] = None,
                     unread_only: Optional[bool] = False):
    emails = fetch_emails(sender, receiver, subject, unread_only)
    return {"emails": emails}


@app.get("/email/{message_id}")
async def get_email_content(request: Request, message_id: str, delete: Optional[bool] = False):
    content = fetch_email_by_message_id(message_id, delete)
    return {"content": content}


if __name__ == "__main__":
    logger.info(datetime.datetime.now())
    uvicorn.run(app, host="0.0.0.0", port=8080)
# pyinstaller -y --clean --additional-hooks-dir extra-hooks main.py --noconsole
