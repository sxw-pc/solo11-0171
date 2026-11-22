import json
from .store import connect,source,repeated,at

def segment(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'segment',file)
        if repeated(db,'segment',d['id'],sid):return {'id':d['id'],'replayed':True}
        houses=d['households']
        if not houses or len(set(houses))!=len(houses) or int(d['diameter_mm'])<=0:raise ValueError('invalid_segment')
        db.execute('INSERT INTO segment VALUES(?,?,?,?,?,?)',(d['id'],d['material'],d['diameter_mm'],json.dumps(houses),'normal',sid))
        return {'id':d['id'],'households':houses}
def gauge(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'gauge',file)
        if repeated(db,'gauge',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM segment WHERE id=?',(d['segment_id'],)).fetchone():raise ValueError('unknown_segment')
        if d['position'] not in ('upstream','downstream'):raise ValueError('invalid_position')
        db.execute('INSERT INTO gauge VALUES(?,?,?,?,?)',(d['id'],d['segment_id'],d['position'],at(d['calibrated_until']),sid))
        return {'id':d['id'],'status':'calibrated'}
def import_reading(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'pressure',file)
        if repeated(db,'pressure',d['id'],sid):return {'id':d['id'],'replayed':True}
        moment=at(d['sampled_at'])
        a=db.execute('SELECT * FROM gauge WHERE id=?',(d['upstream_gauge'],)).fetchone()
        b=db.execute('SELECT * FROM gauge WHERE id=?',(d['downstream_gauge'],)).fetchone()
        if not a or not b or a['id']==b['id'] or a['position']!='upstream' or b['position']!='downstream' or a['segment_id']!=d['segment_id'] or b['segment_id']!=d['segment_id']:
            raise ValueError('independent_segment_gauges_required')
        if a['calibrated_until']<moment or b['calibrated_until']<moment:raise ValueError('gauge_calibration_expired')
        if min(d['upstream_kpa'],d['downstream_kpa'])<0:raise ValueError('negative_pressure')
        prior=db.execute('SELECT MAX(revision) AS r FROM pressure WHERE segment_id=?',(d['segment_id'],)).fetchone()['r']
        if d['revision']!=(prior or 0)+1:raise ValueError('reading_revision_order')
        drop=d['upstream_kpa']-d['downstream_kpa'];status='suspected' if drop>=40 else 'clear'
        db.execute('INSERT INTO pressure VALUES(?,?,?,?,?,?,?,?,?,?)',(d['id'],d['segment_id'],a['id'],b['id'],d['upstream_kpa'],d['downstream_kpa'],moment,d['revision'],status,sid))
        db.execute('INSERT INTO leak(segment_id,pressure_id,status,reason,created_at) VALUES(?,?,?,?,?)',(d['segment_id'],d['id'],status,'drop_'+str(drop),moment))
        db.execute('UPDATE segment SET status=? WHERE id=?',(status,d['segment_id']))
        if status=='clear':
            db.execute("UPDATE repair SET status='disputed' WHERE segment_id=? AND status IN ('isolated','reopened')",(d['segment_id'],))
        return {'id':d['id'],'status':status,'drop_kpa':drop}
