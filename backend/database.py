import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .config import settings

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def init_db():
    with sqlite3.connect(settings.database_path) as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS device_info(device_id TEXT PRIMARY KEY,name TEXT,firmware_version TEXT,online INTEGER,last_heartbeat TEXT,camera_ok INTEGER,mic_ok INTEGER,left_motor_ok INTEGER,right_motor_ok INTEGER,speaker_ok INTEGER,updated_at TEXT);
        CREATE TABLE IF NOT EXISTS obstacle_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,device_id TEXT,detected_at TEXT,object_class TEXT,direction TEXT,confidence REAL,voice_text TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS voice_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,device_id TEXT,occurred_at TEXT,user_text TEXT,reply_text TEXT,request_type TEXT,asr_status TEXT,tts_status TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS system_config(key TEXT PRIMARY KEY,value_json TEXT,scope TEXT,updated_at TEXT);
        CREATE TABLE IF NOT EXISTS system_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,level TEXT,event TEXT,detail TEXT,occurred_at TEXT);
        CREATE TABLE IF NOT EXISTS auth_users(username TEXT PRIMARY KEY COLLATE NOCASE,email TEXT NOT NULL UNIQUE COLLATE NOCASE,password_hash TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS auth_sessions(token_hash TEXT PRIMARY KEY,username TEXT NOT NULL COLLATE NOCASE,expires_at TEXT NOT NULL,created_at TEXT NOT NULL,FOREIGN KEY(username) REFERENCES auth_users(username) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS device_family_bindings(id INTEGER PRIMARY KEY AUTOINCREMENT,device_id TEXT NOT NULL,owner_username TEXT NOT NULL COLLATE NOCASE,family_username TEXT NOT NULL COLLATE NOCASE,can_view_camera INTEGER NOT NULL DEFAULT 1,can_view_location INTEGER NOT NULL DEFAULT 1,can_send_messages INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,UNIQUE(device_id,family_username));
        CREATE TABLE IF NOT EXISTS family_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,device_id TEXT NOT NULL,sender_username TEXT NOT NULL COLLATE NOCASE,recipient_username TEXT NOT NULL COLLATE NOCASE,body TEXT NOT NULL,message_type TEXT NOT NULL DEFAULT 'text',created_at TEXT NOT NULL,read_at TEXT);
        CREATE TABLE IF NOT EXISTS family_access_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,device_id TEXT NOT NULL,actor_username TEXT NOT NULL COLLATE NOCASE,action TEXT NOT NULL,created_at TEXT NOT NULL);
        ''')

@contextmanager
def db():
    c = sqlite3.connect(settings.database_path)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()

def log_system(level, event, detail=""):
    with db() as c:
        c.execute("INSERT INTO system_logs(level,event,detail,occurred_at) VALUES(?,?,?,?)", (level,event,detail,now_iso()))
