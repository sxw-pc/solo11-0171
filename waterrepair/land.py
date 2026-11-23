import json
from .store import connect,source,at,repeated
from . import route_permit

def worksite(path,file):return _save(path,file,'worksite',('id','repair_id','x','y','method'))

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

def parcel(path,file):
    # 地籍按 (id, survey_version) 版本化接收: 同版本同内容幂等, 同版本异内容拒绝, 新版本另起一行;
    # 生效版本由 route_permit 在裁决时按 valid_from 与撤销登记选取。
    with connect(path) as db,db:
        sid,doc,_=source(db,'parcel',file)
        old=db.execute('SELECT source_id FROM parcel WHERE id=? AND survey_version=?',(doc['id'],doc['survey_version'])).fetchone()
        if old:
            if old['source_id']!=sid:raise ValueError('event_id_conflict')
            return {'id':doc['id'],'replayed':True}
        poly=[tuple(map(float,p)) for p in doc['polygon']]
        if len(poly)<3 or not route_permit.is_convex(poly):raise ValueError('parcel_not_convex')
        valid_from=at(doc['valid_from']) if doc.get('valid_from') else None
        db.execute('INSERT INTO parcel VALUES(?,?,?,?,?,?,?,?)',
                   (doc['id'],doc['owner'],json.dumps(doc['polygon'],sort_keys=True),doc['survey_version'],
                    doc.get('crs',route_permit.DEFAULT_CRS),int(doc.get('precision_cm',0)),valid_from,sid))
        route_permit.readjudicate(db)
        return {'id':doc['id'],'replayed':False}

def corridor(path,file):
    with connect(path) as db,db:
        sid,doc,_=source(db,'corridor',file)
        if repeated(db,'corridor',doc['id'],sid):return {'id':doc['id'],'replayed':True}
        db.execute('INSERT INTO corridor VALUES(?,?,?,?,?)',(doc['id'],doc['worksite_id'],json.dumps(doc['path'],sort_keys=True),int(doc['width_cm']),sid))
        route_permit.readjudicate(db)
        return {'id':doc['id'],'replayed':False}

def consent(path,file):
    with connect(path) as db,db:
        sid,doc,_=source(db,'land_consent',file)
        if repeated(db,'land_consent',doc['id'],sid):return {'id':doc['id'],'replayed':True}
        db.execute('INSERT INTO land_consent VALUES(?,?,?,?,?,?,?)',(doc['id'],doc['parcel_id'],doc['worksite_id'],at(doc['valid_from']),at(doc['valid_to']),doc['method'],sid))
        route_permit.readjudicate(db)
        return {'id':doc['id'],'replayed':False}

def route_report(path):
    with connect(path) as db:
        return {'corridors':[dict(x) for x in db.execute('SELECT * FROM corridor ORDER BY id')],
                'consents':[dict(x) for x in db.execute('SELECT * FROM land_consent ORDER BY id')]}
