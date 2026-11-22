import sqlite3,hashlib,json
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime

SCHEMA='''PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS source(id INTEGER PRIMARY KEY,kind TEXT,sha TEXT UNIQUE,raw BLOB);
CREATE TABLE IF NOT EXISTS segment(id TEXT PRIMARY KEY,material TEXT,diameter_mm INTEGER,households_json TEXT,status TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS gauge(id TEXT PRIMARY KEY,segment_id TEXT,position TEXT,calibrated_until TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS pressure(id TEXT PRIMARY KEY,segment_id TEXT,upstream_gauge TEXT,downstream_gauge TEXT,upstream_kpa INTEGER,downstream_kpa INTEGER,sampled_at TEXT,revision INTEGER,status TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS leak(id INTEGER PRIMARY KEY,segment_id TEXT,pressure_id TEXT,status TEXT,reason TEXT,created_at TEXT);
CREATE TABLE IF NOT EXISTS valve(id TEXT PRIMARY KEY,segment_id TEXT,covered_json TEXT,bypass_closed INTEGER,status TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS permit(id TEXT PRIMARY KEY,segment_id TEXT,crew TEXT,material TEXT,max_diameter_mm INTEGER,valid_to TEXT,status TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS repair(id TEXT PRIMARY KEY,segment_id TEXT,valve_id TEXT,permit_id TEXT,started_at TEXT,reopened_at TEXT,test_kpa INTEGER,witness TEXT,status TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS notice(id TEXT PRIMARY KEY,repair_id TEXT,households_json TEXT,delivered_at TEXT,status TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS tap(id TEXT PRIMARY KEY,repair_id TEXT,household TEXT,pressure_kpa INTEGER,observed_at TEXT,witness TEXT,status TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS restoration(id INTEGER PRIMARY KEY,repair_id TEXT,status TEXT,restored_at TEXT,compensation_json TEXT,reason TEXT);
CREATE TABLE IF NOT EXISTS worksite(id TEXT PRIMARY KEY,repair_id TEXT,x INTEGER,y INTEGER,method TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS parcel(id TEXT PRIMARY KEY,owner TEXT,polygon_json TEXT,survey_version TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS corridor(id TEXT PRIMARY KEY,worksite_id TEXT,path_json TEXT,width_cm INTEGER,source_id INTEGER);
CREATE TABLE IF NOT EXISTS land_consent(id TEXT PRIMARY KEY,parcel_id TEXT,worksite_id TEXT,valid_from TEXT,valid_to TEXT,method TEXT,source_id INTEGER);
'''
@contextmanager
def connect(path):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(p);db.row_factory=sqlite3.Row
    try:db.executescript(SCHEMA);yield db
    finally:db.close()
def source(db,kind,file):
    raw=Path(file).read_bytes();digest=hashlib.sha256(raw).hexdigest();doc=json.loads(raw)
    old=db.execute('SELECT id,kind FROM source WHERE sha=?',(digest,)).fetchone()
    if old:
        if old['kind']!=kind:raise ValueError('source_kind_conflict')
        return old['id'],doc,True
    return db.execute('INSERT INTO source(kind,sha,raw) VALUES(?,?,?)',(kind,digest,raw)).lastrowid,doc,False
def at(value):
    if not isinstance(value,str) or not value.endswith('Z'):raise ValueError('utc_required')
    datetime.fromisoformat(value[:-1]+'+00:00');return value
def repeated(db,table,key,sid):
    old=db.execute(f'SELECT source_id FROM {table} WHERE id=?',(key,)).fetchone()
    if old:
        if old['source_id']!=sid:raise ValueError('event_id_conflict')
        return True
    return False
def dump(db):
    return {t:[dict(x) for x in db.execute(f'SELECT * FROM {t} ORDER BY 1')]
            for t in ('segment','gauge','pressure','leak','valve','permit','repair','notice','tap','restoration','worksite','parcel','corridor','land_consent')}
