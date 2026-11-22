import json
from datetime import datetime
from .store import connect,source,repeated,at

def notice(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'notice',file)
        if repeated(db,'notice',d['id'],sid):return {'id':d['id'],'replayed':True}
        repair=db.execute('SELECT * FROM repair WHERE id=?',(d['repair_id'],)).fetchone()
        if not repair:raise ValueError('unknown_repair')
        segment=db.execute('SELECT households_json FROM segment WHERE id=?',(repair['segment_id'],)).fetchone()
        required=set(json.loads(segment['households_json']))
        delivered=set(d['households'])
        if delivered!=required:raise ValueError('notice_households_incomplete')
        moment=at(d['delivered_at'])
        db.execute('INSERT INTO notice VALUES(?,?,?,?,?,?)',(d['id'],repair['id'],json.dumps(sorted(delivered)),moment,'delivered',sid))
        return {'id':d['id'],'status':'delivered','households':len(delivered)}
def tap(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'tap',file)
        if repeated(db,'tap',d['id'],sid):return {'id':d['id'],'replayed':True}
        repair=db.execute('SELECT * FROM repair WHERE id=?',(d['repair_id'],)).fetchone()
        if not repair:raise ValueError('unknown_repair')
        segment=db.execute('SELECT households_json FROM segment WHERE id=?',(repair['segment_id'],)).fetchone()
        if d['household'] not in json.loads(segment['households_json']):raise ValueError('household_outside_segment')
        if d['witness']=='repair_crew' or not d['witness']:raise ValueError('independent_tap_witness_required')
        moment=at(d['observed_at'])
        if moment<repair['started_at']:raise ValueError('tap_before_repair')
        status='restored' if d['pressure_kpa']>=180 else 'low_pressure'
        db.execute('INSERT INTO tap VALUES(?,?,?,?,?,?,?,?)',(d['id'],repair['id'],d['household'],d['pressure_kpa'],moment,d['witness'],status,sid))
        return {'id':d['id'],'status':status}
def decide(path,repair_id):
    with connect(path) as db,db:
        repair=db.execute('SELECT * FROM repair WHERE id=?',(repair_id,)).fetchone()
        if not repair:raise ValueError('unknown_repair')
        houses=set(json.loads(db.execute('SELECT households_json FROM segment WHERE id=?',(repair['segment_id'],)).fetchone()[0]))
        notifications=[x for x in db.execute('SELECT * FROM notice WHERE repair_id=?',(repair_id,))]
        taps=[x for x in db.execute('SELECT * FROM tap WHERE repair_id=?',(repair_id,))]
        good={x['household']:x for x in taps if x['status']=='restored'}
        if repair['status']!='reopened':status,reason='blocked','valve_not_reopened'
        elif not notifications:status,reason='blocked','notice_missing'
        elif not houses.issubset(good):status,reason='blocked','terminal_taps_missing'
        else:status,reason='restored','all_households_proven'
        restored=max((x['observed_at'] for x in good.values()),default=None) if status=='restored' else None
        compensation={}
        if restored:
            minutes=int((datetime.fromisoformat(restored[:-1]+'+00:00')-datetime.fromisoformat(repair['started_at'][:-1]+'+00:00')).total_seconds()//60)
            if minutes>120:compensation={h:minutes-120 for h in houses}
        import hashlib
        seq=db.execute('SELECT COUNT(*) FROM restoration WHERE repair_id=?',(repair_id,)).fetchone()[0]+1
        raw=json.dumps({'repair_id':repair_id,'decision_seq':seq},sort_keys=True).encode()
        db.execute("INSERT INTO source(kind,sha,raw) VALUES('restoration',?,?)",(hashlib.sha256(raw).hexdigest(),raw))
        db.execute('INSERT INTO restoration(repair_id,status,restored_at,compensation_json,reason) VALUES(?,?,?,?,?)',(repair_id,status,restored,json.dumps(compensation,sort_keys=True),reason))
        return {'repair_id':repair_id,'status':status,'reason':reason,'compensation_minutes':compensation}
