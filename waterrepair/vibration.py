import json
from .store import connect,source,at,repeated

def sample(path,file):
    with connect(path) as db:
        sid,doc,_=source(db,'vibration_sample',file)
        if repeated(db,'vibration_sample',doc['id'],sid):return {'id':doc['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM pump WHERE id=?',(doc['pump_id'],)).fetchone():raise ValueError('unknown_pump')
        if not db.execute('SELECT 1 FROM pump_run WHERE id=? AND pump_id=?',(doc['run_id'],doc['pump_id'])).fetchone():raise ValueError('unknown_run')
        if doc['sample_rate_hz']<=0 or not doc['values']:raise ValueError('invalid_sample')
        db.execute('INSERT INTO vibration_sample VALUES(?,?,?,?,?,?,?,?)',(doc['id'],doc['pump_id'],doc['run_id'],doc['sample_rate_hz'],doc['axis'],json.dumps(doc['values']),at(doc['recorded_at']),sid))
        db.commit();return {'id':doc['id'],'replayed':False}

def tach(path,file):
    with connect(path) as db:
        sid,doc,_=source(db,'tachometer',file)
        if repeated(db,'tachometer',doc['id'],sid):return {'id':doc['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM pump_run WHERE id=? AND pump_id=?',(doc['run_id'],doc['pump_id'])).fetchone():raise ValueError('unknown_run')
        db.execute('INSERT INTO tachometer VALUES(?,?,?,?,?,?)',(doc['id'],doc['pump_id'],doc['run_id'],doc['rpm'],at(doc['recorded_at']),sid))
        db.commit();return {'id':doc['id'],'replayed':False}

def captured(path):
    with connect(path) as db:
        return {'samples':[dict(x) for x in db.execute('SELECT * FROM vibration_sample ORDER BY id')],
                'tachometers':[dict(x) for x in db.execute('SELECT * FROM tachometer ORDER BY id')]}
