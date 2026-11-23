from pathlib import Path
from tempfile import TemporaryDirectory
from .store import connect,dump

def report(path):
    with connect(path) as db:return {'state':dump(db),'sources':[{'kind':x['kind'],'sha':x['sha']} for x in db.execute('SELECT kind,sha FROM source ORDER BY id')]}
def audit(path):
    from . import pressure,repair,resident,land,route_permit
    funcs={'segment':pressure.segment,'gauge':pressure.gauge,'pressure':pressure.import_reading,
           'valve':repair.valve,'permit':repair.permit,'repair':repair.isolate,'reopen':repair.reopen,
           'notice':resident.notice,'tap':resident.tap,
           'worksite':land.worksite,'parcel':land.parcel,'corridor':land.corridor,'land_consent':land.consent,
           'gas_line':route_permit.gas_line,'obstacle':route_permit.obstacle,'closure':route_permit.closure,
           'easement':route_permit.easement,'method_spec':route_permit.method_spec,
           'survey_revoke':route_permit.survey_revoke,'easement_withdraw':route_permit.easement_withdraw,
           'route_start':route_permit.start}
    original=report(path)
    with connect(path) as db:raw=[(x['kind'],x['raw']) for x in db.execute('SELECT kind,raw FROM source ORDER BY id')]
    with TemporaryDirectory() as tmp:
        target=Path(tmp)/'replay.sqlite'
        for i,(kind,data) in enumerate(raw):
            if kind=='restoration':
                import json
                resident.decide(target,json.loads(data)['repair_id'])
                continue
            if kind=='route_decide':
                import json
                d=json.loads(data)
                route_permit.decide(target,d['repair_id'],d['at'])
                continue
            f=Path(tmp)/f'{i}.json';f.write_bytes(data);funcs[kind](target,f)
        rebuilt=report(target)
    return {'replay_ok':rebuilt==original,'events':len(raw)}
