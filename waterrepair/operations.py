from .store import connect,source,at,repeated

def _save(path,file,kind,fields):
    with connect(path) as db:
        sid,doc,_=source(db,kind,file)
        if repeated(db,kind,doc['id'],sid):return {'id':doc['id'],'replayed':True}
        for field in ('started_at','stopped_at','window_start','window_end'):
            if field in fields:at(doc[field])
        values=[doc[field] for field in fields]
        db.execute(f"INSERT INTO {kind}({','.join(fields)},source_id) VALUES({','.join('?' for _ in range(len(fields)+1))})",(*values,sid))
        db.commit()
        return {'id':doc['id'],'replayed':False}

def pump(path,file):return _save(path,file,'pump',('id','station','flow_lps','power_kw'))
def tank(path,file):return _save(path,file,'tank',('id','zone','capacity_l','initial_l'))
def run(path,file):return _save(path,file,'pump_run',('id','pump_id','started_at','stopped_at','volume_l','energy_wh'))
def demand(path,file):return _save(path,file,'zone_demand',('id','zone','window_start','window_end','required_l'))

def operation_report(path):
    with connect(path) as db:
        return {'runs':[dict(x) for x in db.execute('SELECT * FROM pump_run ORDER BY id')],
                'demand':[dict(x) for x in db.execute('SELECT * FROM zone_demand ORDER BY id')]}
