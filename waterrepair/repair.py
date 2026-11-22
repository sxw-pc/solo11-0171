import json
from .store import connect,source,repeated,at

def valve(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'valve',file)
        if repeated(db,'valve',d['id'],sid):return {'id':d['id'],'replayed':True}
        segment=db.execute('SELECT * FROM segment WHERE id=?',(d['segment_id'],)).fetchone()
        if not segment:raise ValueError('unknown_segment')
        covered=set(d['covered_households']);required=set(json.loads(segment['households_json']))
        if not covered or not covered.issubset(required):raise ValueError('invalid_valve_topology')
        db.execute('INSERT INTO valve VALUES(?,?,?,?,?,?)',(d['id'],d['segment_id'],json.dumps(sorted(covered)),1 if d['bypass_closed'] else 0,'ready',sid))
        return {'id':d['id'],'coverage_complete':covered==required}
def permit(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'permit',file)
        if repeated(db,'permit',d['id'],sid):return {'id':d['id'],'replayed':True}
        segment=db.execute('SELECT * FROM segment WHERE id=?',(d['segment_id'],)).fetchone()
        if not segment:raise ValueError('unknown_segment')
        if d['material']!=segment['material'] or d['max_diameter_mm']<segment['diameter_mm'] or not d['crew']:
            raise ValueError('crew_qualification_mismatch')
        db.execute('INSERT INTO permit VALUES(?,?,?,?,?,?,?,?)',(d['id'],d['segment_id'],d['crew'],d['material'],d['max_diameter_mm'],at(d['valid_to']),'active',sid))
        return {'id':d['id'],'status':'active'}
def isolate(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'repair',file)
        if repeated(db,'repair',d['id'],sid):return {'id':d['id'],'replayed':True}
        moment=at(d['started_at'])
        segment=db.execute('SELECT * FROM segment WHERE id=?',(d['segment_id'],)).fetchone()
        valve=db.execute('SELECT * FROM valve WHERE id=?',(d['valve_id'],)).fetchone()
        permit=db.execute('SELECT * FROM permit WHERE id=?',(d['permit_id'],)).fetchone()
        if not segment or not valve or not permit or valve['segment_id']!=segment['id'] or permit['segment_id']!=segment['id']:
            raise ValueError('repair_inputs_missing')
        if segment['status']!='suspected':raise ValueError('leak_not_suspected')
        if set(json.loads(valve['covered_json']))!=set(json.loads(segment['households_json'])):raise ValueError('valve_coverage_incomplete')
        if not valve['bypass_closed']:raise ValueError('bypass_open')
        if permit['valid_to']<moment:raise ValueError('permit_expired')
        if db.execute("SELECT 1 FROM repair WHERE segment_id=? AND status IN ('isolated','reopened')",(segment['id'],)).fetchone():raise ValueError('active_repair_exists')
        db.execute('INSERT INTO repair VALUES(?,?,?,?,?,?,?,?,?,?)',(d['id'],segment['id'],valve['id'],permit['id'],moment,None,None,None,'isolated',sid))
        db.execute("UPDATE valve SET status='closed' WHERE id=?",(valve['id'],))
        return {'id':d['id'],'status':'isolated','affected_households':json.loads(segment['households_json'])}
def reopen(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'reopen',file)
        repair=db.execute('SELECT * FROM repair WHERE id=?',(d['repair_id'],)).fetchone()
        if not repair or repair['status']!='isolated':raise ValueError('repair_not_isolated')
        if d['witness']==d['crew'] or not d['witness']:raise ValueError('independent_pressure_witness_required')
        moment=at(d['at'])
        if moment<=repair['started_at'] or d['test_kpa']<200:raise ValueError('pressure_test_failed')
        db.execute("UPDATE repair SET status='reopened',reopened_at=?,test_kpa=?,witness=? WHERE id=?",(moment,d['test_kpa'],d['witness'],repair['id']))
        db.execute("UPDATE valve SET status='open' WHERE id=?",(repair['valve_id'],))
        return {'repair_id':repair['id'],'status':'reopened'}
