from .store import connect,source,at,repeated

def test_point(path,file):
    with connect(path) as db:
        sid,doc,_=source(db,'pump_test',file)
        if repeated(db,'pump_test',doc['id'],sid):return {'id':doc['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM pump_run WHERE id=? AND pump_id=?',(doc['run_id'],doc['pump_id'])).fetchone():raise ValueError('unknown_run')
        if doc['phase'] not in ('before','after'):raise ValueError('invalid_phase')
        if min(doc['head_kpa'],doc['volume_l'],doc['energy_wh'])<=0:raise ValueError('invalid_test')
        db.execute('INSERT INTO pump_test VALUES(?,?,?,?,?,?,?,?,?)',(doc['id'],doc['pump_id'],doc['run_id'],doc['phase'],doc['head_kpa'],doc['volume_l'],doc['energy_wh'],at(doc['measured_at']),sid))
        db.commit();return {'id':doc['id'],'replayed':False}

def points(path):
    with connect(path) as db:return [dict(x) for x in db.execute('SELECT * FROM pump_test ORDER BY id')]
