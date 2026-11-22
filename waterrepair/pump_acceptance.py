import hashlib,json
from .store import connect,source,repeated,at

# Hydraulic work in Wh for pressure head H (kPa) acting on volume V (litres):
# 1 kPa*l = 1 kJ; /3600 converts kJ to Wh.
WH_PER_KPA_L=1.0/3600.0

def case(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'acceptance_case',file)
        if repeated(db,'acceptance_case',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM pump WHERE id=?',(d['pump_id'],)).fetchone():raise ValueError('unknown_pump')
        if int(d['reference_head_kpa'])<=0 or int(d['reference_volume_l'])<=0 or int(d['guarantee_energy_wh'])<=0 or int(d['safe_max_head_kpa'])<=0:raise ValueError('invalid_case')
        db.execute('INSERT INTO acceptance_case VALUES(?,?,?,?,?,?,?)',(d['id'],d['pump_id'],d['reference_head_kpa'],d['reference_volume_l'],d['guarantee_energy_wh'],d['safe_max_head_kpa'],sid))
        return {'id':d['id'],'pump_id':d['pump_id'],'replayed':False}

def meter(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'meter',file)
        if repeated(db,'meter',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM pump WHERE id=?',(d['pump_id'],)).fetchone():raise ValueError('unknown_pump')
        if d['meter_kind'] not in ('energy','flow','head','speed'):raise ValueError('invalid_meter_kind')
        if d['scope'] not in ('pump','shared_bus'):raise ValueError('invalid_meter_scope')
        db.execute('INSERT INTO meter VALUES(?,?,?,?,?,?)',(d['id'],d['pump_id'],d['meter_kind'],d['scope'],at(d['calibrated_until']),sid))
        return {'id':d['id'],'meter_kind':d['meter_kind'],'replayed':False}

def bus_share(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'bus_allocation',file)
        if repeated(db,'bus_allocation',d['id'],sid):return {'id':d['id'],'replayed':True}
        m=db.execute('SELECT * FROM meter WHERE id=?',(d['meter_id'],)).fetchone()
        if not m:raise ValueError('unknown_meter')
        if m['scope']!='shared_bus':raise ValueError('meter_not_shared_bus')
        if not db.execute('SELECT 1 FROM pump WHERE id=?',(d['pump_id'],)).fetchone():raise ValueError('unknown_pump')
        if not (0.0<float(d['share'])<=1.0):raise ValueError('invalid_share')
        w0,w1=at(d['window_start']),at(d['window_end'])
        if w1<=w0:raise ValueError('invalid_window')
        rows=db.execute('SELECT share FROM bus_allocation WHERE meter_id=? AND window_start=? AND window_end=?',(d['meter_id'],w0,w1)).fetchall()
        if sum(x['share'] for x in rows)+float(d['share'])>1.0000001:raise ValueError('bus_shares_exceed_one')
        db.execute('INSERT INTO bus_allocation VALUES(?,?,?,?,?,?,?)',(d['id'],d['meter_id'],d['pump_id'],d['share'],w0,w1,sid))
        return {'id':d['id'],'meter_id':d['meter_id'],'replayed':False}

def evidence(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'test_evidence',file)
        if repeated(db,'test_evidence',d['id'],sid):return {'id':d['id'],'replayed':True}
        t=db.execute('SELECT * FROM pump_test WHERE id=?',(d['test_id'],)).fetchone()
        if not t:raise ValueError('unknown_test')
        m=db.execute('SELECT * FROM meter WHERE id=?',(d['meter_id'],)).fetchone()
        if not m:raise ValueError('unknown_meter')
        if m['pump_id']!=t['pump_id']:raise ValueError('meter_pump_mismatch')
        if m['meter_kind']!=d['meter_kind'] or d['meter_kind'] not in ('energy','flow','head','speed'):raise ValueError('meter_kind_mismatch')
        w0,w1=at(d['stable_window_start']),at(d['stable_window_end'])
        if w1<=w0:raise ValueError('invalid_window')
        if not (t['measured_at']>=w0 and t['measured_at']<=w1):raise ValueError('test_outside_stable_window')
        run=db.execute('SELECT * FROM pump_run WHERE id=?',(t['run_id'],)).fetchone()
        if w0<run['started_at'] or w1>run['stopped_at']:raise ValueError('window_outside_run')
        if not bool(d['startup_excluded']):raise ValueError('startup_transient_not_excluded')
        if not bool(d.get('calibration_ok',True)):raise ValueError('meter_calibration_expired')
        parallel=list(d.get('parallel_pumps',[]))
        if len(parallel)!=len(set(parallel)):raise ValueError('duplicate_parallel_pump')
        val=float(d['reading_value'])
        if val<=0:raise ValueError('invalid_reading')
        # Readings must trace to the recorded test point. A shared-bus energy
        # meter reads the whole busbar: the pump's own consumption is its
        # allocated share of that reading and must equal the test energy, so
        # VFD losses and parallel pumps are never silently attributed to P1.
        if d['meter_kind']=='energy':
            if m['scope']=='shared_bus':
                alloc=db.execute('SELECT share FROM bus_allocation WHERE meter_id=? AND pump_id=? AND window_start<=? AND window_end>=?',(m['id'],t['pump_id'],w0,w1)).fetchone()
                if not alloc:raise ValueError('missing_bus_allocation')
                attributed=round(val*alloc['share'],6)
                if abs(attributed-float(t['energy_wh']))>max(1e-6,float(t['energy_wh'])*1e-9):raise ValueError('bus_allocation_mismatch')
            elif abs(val-float(t['energy_wh']))>1e-6:raise ValueError('energy_reading_mismatch')
        elif d['meter_kind']=='flow' and abs(val-float(t['volume_l']))>1e-6:raise ValueError('flow_reading_mismatch')
        elif d['meter_kind']=='head' and abs(val-float(t['head_kpa']))>1e-6:raise ValueError('head_reading_mismatch')
        elif d['meter_kind']=='speed' and float(d.get('speed_hz',0.0))<=0:raise ValueError('invalid_speed')
        db.execute('INSERT INTO test_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(d['id'],t['id'],m['id'],d['meter_kind'],val,d['unit'],w0,w1,1,json.dumps(sorted(parallel)),d.get('speed_hz'),sid))
        return {'id':d['id'],'test_id':t['id'],'replayed':False}

def calibration(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'calibration_correction',file)
        if repeated(db,'calibration_correction',d['id'],sid):return {'id':d['id'],'replayed':True}
        m=db.execute('SELECT * FROM meter WHERE id=?',(d['meter_id'],)).fetchone()
        if not m:raise ValueError('unknown_meter')
        moment=at(d['corrected_at']);prev=at(d['previous_calibrated_until']);new=at(d['new_calibrated_until'])
        if not isinstance(d.get('invalidates_readings'),bool):raise ValueError('invalid_correction_flag')
        db.execute('INSERT INTO calibration_correction VALUES(?,?,?,?,?,?,?,?)',(d['id'],m['id'],moment,prev,new,1 if d['invalidates_readings'] else 0,d.get('reason',''),sid))
        withdrawn=[]
        if d['invalidates_readings']:
            # Any certificate whose evidence chain rests on this meter in a
            # window the correction invalidates must be retracted, never edited.
            rows=db.execute('''SELECT DISTINCT c.* FROM pump_certificate c
                WHERE c.status IN ('trial','limited_field','formal')
                AND EXISTS(SELECT 1 FROM test_evidence e JOIN pump_test t ON t.id=e.test_id
                           WHERE e.meter_id=? AND t.pump_id=c.pump_id)''',(m['id'],)).fetchall()
            for c in rows:
                wid=f"W-{c['pump_id']}-{c['authority']}"
                if db.execute('SELECT 1 FROM pump_certificate WHERE id=?',(wid,)).fetchone():continue
                latest=db.execute("SELECT id,version FROM pump_certificate WHERE pump_id=? AND authority=? AND status!='withdrawn' ORDER BY version DESC,id DESC LIMIT 1",(c['pump_id'],c['authority'])).fetchone()
                wver=latest['version']+1
                raw=json.dumps({'withdrawal':wid,'certificate':latest['id'],'correction':d['id']},sort_keys=True).encode()
                wsid=db.execute('INSERT INTO source(kind,sha,raw) VALUES(?,?,?)',('certificate_withdrawal',hashlib.sha256(raw).hexdigest(),raw)).lastrowid
                text=f"CERTIFICATE {latest['id']} WITHDRAWN: meter {m['id']} calibration correction {d['id']} invalidated the evidence chain; field operation under the limited condition remains a fact on record."
                db.execute('INSERT INTO pump_certificate VALUES(?,?,?,?,?,?,?,?,?,?)',(wid,c['pump_id'],c['authority'],wver,'withdrawn',c['condition_json'],text,latest['id'],moment,wsid))
                withdrawn.append(wid)
        return {'id':d['id'],'meter_id':m['id'],'withdrawn':withdrawn,'replayed':False}

# ---------- physics: feasible envelope at the contract reference point -----

def point_bounds(point,ref_head):
    """Feasible reference-energy interval implied by ONE measured point.

    Lower edge: real losses fixed, only hydraulic work grows with head
    (constant absolute losses -> the most efficient admissible curve).
    Upper edge: efficiency fixed, energy scales with head (the naive
    proportional formula -> the least efficient admissible curve).
    """
    h,e,v=float(point['head_kpa']),float(point['energy_wh']),float(point['volume_l'])
    hydraulic=h*v*WH_PER_KPA_L
    if e<hydraulic-1e-9:raise ValueError('energy_below_hydraulic_floor')
    low=e+(ref_head-h)*v*WH_PER_KPA_L
    high=e*ref_head/h
    if low>high+1e-9:raise ValueError('infeasible_point_bounds')
    return round(low,4),round(high,4)

def reference_envelope(points,ref_head):
    if not points:raise ValueError('no_points')
    lo,hi,used=0.0,float('inf'),[]
    for p in points:
        l,u=point_bounds(p,ref_head)
        lo=max(lo,l);hi=min(hi,u);used.append(p['id'])
        if lo>hi+1e-9:raise ValueError('curve_points_inconsistent')
    return {'lower_wh':round(lo,4),'upper_wh':round(hi,4),'point_ids':used}

def supplemental_plan(env,case_row):
    g=float(case_row['guarantee_energy_wh']);v=float(case_row['reference_volume_l'])
    href=int(case_row['reference_head_kpa'])
    target=min(int(case_row['safe_max_head_kpa']),href)
    k=v*WH_PER_KPA_L
    # E_t <= (G - (H_ref-H_t)*V/3600) keeps the whole envelope <= G.
    pass_below=round(g-(href-target)*k,4)
    # E_t >  (G*H_t/H_ref) proves every admissible curve exceeds G.
    fail_above=round(g*target/href,4)
    # Between the two edges neither sign is provable: the reading stays in the
    # pending band instead of being forced into a certificate by averaging.
    pending_lo=min(pass_below,fail_above);pending_hi=max(pass_below,fail_above)
    return {'recommended_head_kpa':target,'pass_if_energy_wh_at_most':pass_below,'fail_if_energy_wh_above':fail_above,
            'pending_band_wh':[round(pending_lo,4),round(pending_hi,4)]}

def _active_calibration(db,meter_id,moment):
    m=db.execute('SELECT * FROM meter WHERE id=?',(meter_id,)).fetchone()
    cutoff=m['calibrated_until'];invalidated=False
    # Corrections apply retroactively to the meter's whole evidence chain:
    # a drift discovered later still invalidates readings taken earlier.
    for c in db.execute('SELECT * FROM calibration_correction WHERE meter_id=? ORDER BY corrected_at,id',(meter_id,)):
        if c['invalidates_readings']:invalidated=True
        cutoff=c['new_calibrated_until']
    return cutoff,invalidated

def evaluate(path,case_id):
    with connect(path) as db:
        c=db.execute('SELECT * FROM acceptance_case WHERE id=?',(case_id,)).fetchone()
        if not c:raise ValueError('unknown_case')
        tests=[dict(x) for x in db.execute('SELECT * FROM pump_test WHERE pump_id=? AND volume_l=? ORDER BY measured_at,id',(c['pump_id'],c['reference_volume_l']))]
        valid=[];evidence_issues=[]
        for t in tests:
            evs=db.execute('SELECT * FROM test_evidence WHERE test_id=? ORDER BY meter_kind,id',(t['id'],)).fetchall()
            kinds={e['meter_kind'] for e in evs}
            missing={'energy','flow','head'}-kinds
            if missing:evidence_issues.append({'test_id':t['id'],'reason':'missing_meter_evidence','meter_kinds':sorted(missing)})
            bad=False
            for e in evs:
                cutoff,invalidated=_active_calibration(db,e['meter_id'],t['measured_at'])
                if invalidated or cutoff<t['measured_at']:
                    evidence_issues.append({'test_id':t['id'],'reason':'meter_calibration_expired','meter_id':e['meter_id']});bad=True
                if e['stable_window_start']<db.execute('SELECT started_at FROM pump_run WHERE id=?',(t['run_id'],)).fetchone()['started_at']:
                    evidence_issues.append({'test_id':t['id'],'reason':'window_outside_run','meter_id':e['meter_id']});bad=True
            if not bad and not missing:valid.append(t)
        after=[t for t in valid if t['phase']=='after']
        before=[t for t in valid if t['phase']=='before']
        envelope=reference_envelope(after,c['reference_head_kpa']) if after else None
        g=float(c['guarantee_energy_wh'])
        if envelope is None:factory='no_after_point'
        elif envelope['upper_wh']<=g+1e-9:factory='pass'
        elif envelope['lower_wh']>g+1e-9:factory='fail'
        else:factory='indeterminate'
        plan=supplemental_plan(envelope,c) if envelope and factory=='indeterminate' else None
        field=None
        b_ref=[t for t in before if t['head_kpa']==c['reference_head_kpa']]
        a_field=[t for t in after if t['head_kpa']!=c['reference_head_kpa']]
        if b_ref and a_field:
            b=min(b_ref,key=lambda x:x['measured_at']);a=min(a_field,key=lambda x:x['measured_at'])
            if a['volume_l']==b['volume_l']:
                saving=round((b['energy_wh']-a['energy_wh'])/b['energy_wh'],6)
                field={'field_head_kpa':a['head_kpa'],'before_energy_wh':b['energy_wh'],'after_energy_wh':a['energy_wh'],
                       'field_saving_ratio':saving,'points':[b['id'],a['id']]}
        return {'case_id':c['id'],'pump_id':c['pump_id'],'reference':{'head_kpa':c['reference_head_kpa'],'volume_l':c['reference_volume_l'],'guarantee_energy_wh':c['guarantee_energy_wh']},
                'reference_envelope_wh':envelope,'factory_guarantee':factory,'supplemental_plan':plan,
                'field_pilot':field,'evidence_issues':evidence_issues,'valid_point_ids':[t['id'] for t in valid]}

def publish(path,case_id):
    with connect(path) as db,db:
        c=db.execute('SELECT * FROM acceptance_case WHERE id=?',(case_id,)).fetchone()
        if not c:raise ValueError('unknown_case')
        result=evaluate(path,case_id)
        # Anchor the certificate timestamp to the raw test ledger, not to the
        # currently-valid subset, so a later calibration withdrawal cannot
        # shift a re-published certificate's timestamp and break replay.
        moments=[x['measured_at'] for x in db.execute('SELECT measured_at FROM pump_test WHERE pump_id=?',(c['pump_id'],))]
        if not moments:raise ValueError('no_test_data')
        decision_time=max(moments)
        out=[]
        def write(authority,status,condition,text):
            last=db.execute("SELECT version,status FROM pump_certificate WHERE pump_id=? AND authority=? AND status!='withdrawn' ORDER BY version DESC,id DESC LIMIT 1",(c['pump_id'],authority)).fetchone()
            if last and last['status']==status:return None
            maxver=db.execute('SELECT MAX(version) AS v FROM pump_certificate WHERE pump_id=? AND authority=?',(c['pump_id'],authority)).fetchone()['v']
            version=(maxver or 0)+1
            cid=f"C-{c['pump_id']}-{authority}-v{version}"
            supersedes=db.execute("SELECT id FROM pump_certificate WHERE pump_id=? AND authority=? AND status!='withdrawn' ORDER BY version DESC,id DESC LIMIT 1",(c['pump_id'],authority)).fetchone()
            raw=json.dumps({'certificate':cid,'case':case_id,'status':status,'condition':condition,'version':version},sort_keys=True).encode()
            sid=db.execute('INSERT INTO source(kind,sha,raw) VALUES(?,?,?)',('pump_certificate',hashlib.sha256(raw).hexdigest(),raw)).lastrowid
            db.execute('INSERT INTO pump_certificate VALUES(?,?,?,?,?,?,?,?,?,?)',(cid,c['pump_id'],authority,version,status,json.dumps(condition,sort_keys=True),text,supersedes['id'] if supersedes else None,decision_time,sid))
            return cid
        # Authority A: manufacturer reference-point guarantee (300 kPa / 72000 l).
        f=result['factory_guarantee'];env=result['reference_envelope_wh']
        if f=='pass':
            cid=write('manufacturer','formal',{'basis':'reference_envelope_within_guarantee','envelope_wh':env},
                      f"FORMAL PERFORMANCE CERTIFICATE: pump {c['pump_id']} proven at {c['reference_head_kpa']} kPa / {c['reference_volume_l']} l, feasible energy interval {env['lower_wh']}-{env['upper_wh']} Wh within the {c['guarantee_energy_wh']} Wh (15% saving) guarantee.")
        elif f=='fail':
            cid=write('manufacturer','denied',{'basis':'reference_envelope_above_guarantee','envelope_wh':env},
                      f"GUARANTEE DENIED: pump {c['pump_id']} feasible reference energy interval {env['lower_wh']}-{env['upper_wh']} Wh exceeds the {c['guarantee_energy_wh']} Wh guarantee at {c['reference_head_kpa']} kPa; contractor remedial obligation remains open.")
        else:
            measured=db.execute("SELECT * FROM pump_test WHERE pump_id=? AND phase='after' ORDER BY head_kpa,id LIMIT 1",(c['pump_id'],)).fetchone()
            if not measured:raise ValueError('no_after_point')
            cond={'basis':'single_off_reference_point','measured_head_kpa':measured['head_kpa'],'measured_energy_wh':measured['energy_wh']}
            if env:cond['reference_envelope_wh']=env
            if result['supplemental_plan']:cond['supplemental_plan']=result['supplemental_plan']
            cid=write('manufacturer','trial',cond,
                      f"TRIAL OPERATION ONLY: the {measured['head_kpa']} kPa point cannot be signed as the {c['reference_head_kpa']} kPa 15% guarantee; feasible reference energy interval {env['lower_wh'] if env else 'n/a'}-{env['upper_wh'] if env else 'n/a'} Wh straddles the {c['guarantee_energy_wh']} Wh limit. Contractor must complete the {c['reference_head_kpa']} kPa supplemental test before a formal certificate can issue.")
        if cid:out.append(cid)
        # Authority B: municipal field energy-saving pilot (measured field condition only).
        fp=result['field_pilot']
        if fp:
            cid=write('municipal_field','limited_field',{'field_head_kpa':fp['field_head_kpa'],'before_energy_wh':fp['before_energy_wh'],'after_energy_wh':fp['after_energy_wh'],'field_saving_ratio':fp['field_saving_ratio']},
                      f"LIMITED FIELD COMMISSIONING CERTIFICATE: field saving {fp['before_energy_wh']}->{fp['after_energy_wh']} Wh ({round(fp['field_saving_ratio']*100,2)}%) verified ONLY at the current {fp['field_head_kpa']} kPa operating condition. This is not the manufacturer {c['reference_head_kpa']} kPa guarantee and must not be transcribed as such.")
            if cid:out.append(cid)
        return {'case_id':case_id,'pump_id':c['pump_id'],'factory_guarantee':f,'certificates':out,'evaluation':result}

def certificates(path,pump_id=None):
    with connect(path) as db:
        q='SELECT * FROM pump_certificate';args=()
        if pump_id:q+=' WHERE pump_id=?';args=(pump_id,)
        return [dict(x) for x in db.execute(q+' ORDER BY pump_id,authority,version',args)]

def acceptance_report(path):
    with connect(path) as db:
        cases=[dict(x) for x in db.execute('SELECT * FROM acceptance_case ORDER BY id')]
    return {'cases':[evaluate(path,c['id']) for c in cases],'certificates':certificates(path)}
