from pathlib import Path
from tempfile import TemporaryDirectory
from .store import connect,dump

def report(path):
    with connect(path) as db:return {'state':dump(db),'sources':[{'kind':x['kind'],'sha':x['sha']} for x in db.execute('SELECT kind,sha FROM source ORDER BY id')]}
def audit(path):
    from . import pressure,repair,resident,operations,performance,pump_acceptance
    funcs={'segment':pressure.segment,'gauge':pressure.gauge,'pressure':pressure.import_reading,
           'valve':repair.valve,'permit':repair.permit,'repair':repair.isolate,'reopen':repair.reopen,
           'notice':resident.notice,'tap':resident.tap,'pump':operations.pump,
           'tank':operations.tank,'pump_run':operations.run,'zone_demand':operations.demand,
           'pump_test':performance.test_point,
           'acceptance_case':pump_acceptance.case,'meter':pump_acceptance.meter,
           'bus_allocation':pump_acceptance.bus_share,'test_evidence':pump_acceptance.evidence,
           'calibration_correction':pump_acceptance.calibration}
    original=report(path)
    with connect(path) as db:raw=[(x['kind'],x['raw']) for x in db.execute('SELECT kind,raw FROM source ORDER BY id')]
    with TemporaryDirectory() as tmp:
        target=Path(tmp)/'replay.sqlite'
        for i,(kind,data) in enumerate(raw):
            if kind=='restoration':
                import json
                resident.decide(target,json.loads(data)['repair_id'])
                continue
            if kind=='pump_certificate':
                import json
                pump_acceptance.publish(target,json.loads(data)['case'])
                continue
            if kind=='certificate_withdrawal':
                # Derived deterministically by replaying the calibration
                # correction that owns this source; skip the derived row.
                continue
            f=Path(tmp)/f'{i}.json';f.write_bytes(data);funcs[kind](target,f)
        rebuilt=report(target)
    return {'replay_ok':rebuilt==original,'events':len(raw)}
