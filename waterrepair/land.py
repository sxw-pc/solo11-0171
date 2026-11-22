import json
from .store import connect,source,at,repeated

def _save(path,file,kind,fields,encoded=()):
    with connect(path) as db:
        sid,doc,_=source(db,kind,file)
        if repeated(db,kind,doc['id'],sid):return {'id':doc['id'],'replayed':True}
        values=[json.dumps(doc[key],sort_keys=True) if key in encoded else doc[key] for key in fields]
        for key in ('valid_from','valid_to'):
            if key in fields:at(doc[key])
        cols=[key+'_json' if key in encoded else key for key in fields]
        db.execute(f"INSERT INTO {kind}({','.join(cols)},source_id) VALUES({','.join('?' for _ in range(len(fields)+1))})",(*values,sid))
        db.commit()
        return {'id':doc['id'],'replayed':False}

def worksite(path,file):return _save(path,file,'worksite',('id','repair_id','x','y','method'))
def parcel(path,file):return _save(path,file,'parcel',('id','owner','polygon','survey_version'),('polygon',))
def corridor(path,file):return _save(path,file,'corridor',('id','worksite_id','path','width_cm'),('path',))
def consent(path,file):return _save(path,file,'land_consent',('id','parcel_id','worksite_id','valid_from','valid_to','method'))

def route_report(path):
    with connect(path) as db:
        return {'corridors':[dict(x) for x in db.execute('SELECT * FROM corridor ORDER BY id')],
                'consents':[dict(x) for x in db.execute('SELECT * FROM land_consent ORDER BY id')]}
