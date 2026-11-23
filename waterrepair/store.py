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
CREATE TABLE IF NOT EXISTS parcel(id TEXT,owner TEXT,polygon_json TEXT,survey_version TEXT,crs TEXT,precision_cm INTEGER,valid_from TEXT,source_id INTEGER,PRIMARY KEY(id,survey_version));
CREATE TABLE IF NOT EXISTS corridor(id TEXT PRIMARY KEY,worksite_id TEXT,path_json TEXT,width_cm INTEGER,source_id INTEGER);
CREATE TABLE IF NOT EXISTS land_consent(id TEXT PRIMARY KEY,parcel_id TEXT,worksite_id TEXT,valid_from TEXT,valid_to TEXT,method TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS gas_line(id TEXT PRIMARY KEY,path_json TEXT,belt_cm INTEGER,crs TEXT,precision_cm INTEGER,survey_version TEXT,valid_from TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS obstacle(id TEXT PRIMARY KEY,polygon_json TEXT,crs TEXT,precision_cm INTEGER,survey_version TEXT,valid_from TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS closure(id TEXT PRIMARY KEY,parcel_id TEXT,lane TEXT,method TEXT,quota INTEGER,window_from TEXT,window_to TEXT,authority TEXT,cost TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS easement(id TEXT PRIMARY KEY,parcel_id TEXT,worksite_id TEXT,method TEXT,valid_from TEXT,valid_to TEXT,daily_from TEXT,daily_to TEXT,compensation_json TEXT,status TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS method_spec(id TEXT PRIMARY KEY,machine_width_cm INTEGER,min_turn_radius_m REAL,swing_cm INTEGER,source_id INTEGER);
CREATE TABLE IF NOT EXISTS survey_revocation(id TEXT PRIMARY KEY,survey_version TEXT,revoked_at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS easement_withdrawal(id TEXT PRIMARY KEY,easement_id TEXT,withdrawn_at TEXT,source_id INTEGER);
CREATE TABLE IF NOT EXISTS route_permit(id INTEGER PRIMARY KEY,worksite_id TEXT,seq INTEGER,status TEXT,decided_at TEXT,detail_json TEXT);
CREATE TABLE IF NOT EXISTS route_start(id TEXT PRIMARY KEY,worksite_id TEXT,corridor_id TEXT,method TEXT,started_at TEXT,source_id INTEGER);
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
            for t in ('segment','gauge','pressure','leak','valve','permit','repair','notice','tap','restoration','worksite','parcel','corridor','land_consent',
                      'gas_line','obstacle','closure','easement','method_spec','survey_revocation','easement_withdrawal','route_permit','route_start')}
