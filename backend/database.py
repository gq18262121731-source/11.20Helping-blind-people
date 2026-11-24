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
        CREATE TABLE IF NOT EXISTS multimodal_tasks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            voice_interaction_id INTEGER,
            device_id TEXT,
            source_id TEXT,
            source_mode TEXT NOT NULL DEFAULT 'software',
            frame_seq INTEGER,
            intent TEXT NOT NULL,
            intent_confidence REAL NOT NULL DEFAULT 0,
            detection_snapshot TEXT,
            ocr_result TEXT,
            vlm_scene_description TEXT,
            ocr_model TEXT,
            vlm_model TEXT,
            final_reply TEXT,
            response_basis TEXT,
            asr_status TEXT,
            intent_status TEXT,
            yolo_status TEXT,
            vlm_status TEXT,
            reply_status TEXT,
            tts_target TEXT,
            tts_status TEXT,
            tts_request_id INTEGER,
            stage_latency_ms TEXT,
            error_code TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_multimodal_tasks_created_at ON multimodal_tasks(id DESC);
        CREATE INDEX IF NOT EXISTS idx_multimodal_tasks_source ON multimodal_tasks(source_mode,source_id,id DESC);
        CREATE TABLE IF NOT EXISTS system_config(key TEXT PRIMARY KEY,value_json TEXT,scope TEXT,updated_at TEXT);
        CREATE TABLE IF NOT EXISTS system_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,level TEXT,event TEXT,detail TEXT,occurred_at TEXT);
        CREATE TABLE IF NOT EXISTS auth_users(username TEXT PRIMARY KEY COLLATE NOCASE,email TEXT NOT NULL UNIQUE COLLATE NOCASE,password_hash TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS auth_sessions(token_hash TEXT PRIMARY KEY,username TEXT NOT NULL COLLATE NOCASE,expires_at TEXT NOT NULL,created_at TEXT NOT NULL,FOREIGN KEY(username) REFERENCES auth_users(username) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS device_family_bindings(id INTEGER PRIMARY KEY AUTOINCREMENT,device_id TEXT NOT NULL,owner_username TEXT NOT NULL COLLATE NOCASE,family_username TEXT NOT NULL COLLATE NOCASE,can_view_camera INTEGER NOT NULL DEFAULT 1,can_view_location INTEGER NOT NULL DEFAULT 1,can_send_messages INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,UNIQUE(device_id,family_username));
        CREATE TABLE IF NOT EXISTS family_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,device_id TEXT NOT NULL,sender_username TEXT NOT NULL COLLATE NOCASE,recipient_username TEXT NOT NULL COLLATE NOCASE,body TEXT NOT NULL,message_type TEXT NOT NULL DEFAULT 'text',created_at TEXT NOT NULL,read_at TEXT);
        CREATE TABLE IF NOT EXISTS family_access_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,device_id TEXT NOT NULL,actor_username TEXT NOT NULL COLLATE NOCASE,action TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS location_points(id INTEGER PRIMARY KEY AUTOINCREMENT,device_id TEXT NOT NULL,latitude REAL NOT NULL,longitude REAL NOT NULL,accuracy REAL,source TEXT NOT NULL,mode TEXT NOT NULL,captured_at TEXT NOT NULL,sequence INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_location_points_device_time ON location_points(device_id,id DESC);
        CREATE TABLE IF NOT EXISTS guard_sharing(device_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS guard_location_provider(device_id TEXT PRIMARY KEY,source TEXT NOT NULL,started_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS guard_events(id INTEGER PRIMARY KEY AUTOINCREMENT,device_id TEXT NOT NULL,event_type TEXT NOT NULL,title TEXT NOT NULL,detail TEXT NOT NULL,occurred_at TEXT NOT NULL,source_mode TEXT NOT NULL DEFAULT 'real');
        CREATE INDEX IF NOT EXISTS idx_guard_events_device_time ON guard_events(device_id,id DESC);
        ''')
        columns = {row[1] for row in c.execute("PRAGMA table_info(obstacle_logs)").fetchall()}
        for name, declaration in (
            ("risk_level", "TEXT"), ("risk_score", "REAL"),
            ("risk_reasons", "TEXT"), ("delivery_status", "TEXT"),
        ):
            if name not in columns:
                c.execute(f"ALTER TABLE obstacle_logs ADD COLUMN {name} {declaration}")
        task_columns = {row[1] for row in c.execute("PRAGMA table_info(multimodal_tasks)").fetchall()}
        if "tts_request_id" not in task_columns:
            c.execute("ALTER TABLE multimodal_tasks ADD COLUMN tts_request_id INTEGER")
        guard_event_columns = {row[1] for row in c.execute("PRAGMA table_info(guard_events)").fetchall()}
        if "source_mode" not in guard_event_columns:
            c.execute("ALTER TABLE guard_events ADD COLUMN source_mode TEXT NOT NULL DEFAULT 'unknown'")

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
