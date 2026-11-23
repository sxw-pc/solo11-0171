import argparse,json,tempfile
from pathlib import Path
from . import pressure,repair,resident,land,route_permit
from .report import report,audit
COMMANDS={'segment-import':pressure.segment,'gauge-import':pressure.gauge,'pressure-import':pressure.import_reading,
          'valve-import':repair.valve,'permit-import':repair.permit,'repair-import':repair.isolate,
          'reopen-import':repair.reopen,'notice-import':resident.notice,'tap-import':resident.tap,
          'worksite-import':land.worksite,'parcel-import':land.parcel,
          'corridor-import':land.corridor,'land-consent-import':land.consent,
          'gas-line-import':route_permit.gas_line,'obstacle-import':route_permit.obstacle,
          'closure-import':route_permit.closure,'easement-import':route_permit.easement,
          'method-spec-import':route_permit.method_spec,'survey-revoke':route_permit.survey_revoke,
          'easement-withdraw':route_permit.easement_withdraw,'route-start-import':route_permit.start}
def main():
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='cmd',required=True)
    for name in COMMANDS:
        x=s.add_parser(name);x.add_argument('file');x.add_argument('--db',required=True)
    d=s.add_parser('restore-decide');d.add_argument('--db',required=True);d.add_argument('--repair',required=True)
    d=s.add_parser('route-permit-decide');d.add_argument('--db',required=True);d.add_argument('--repair',required=True);d.add_argument('--at',required=True)
    for name in ('report','audit'):
        x=s.add_parser(name);x.add_argument('--db',required=True)
    x=s.add_parser('route-report');x.add_argument('--db',required=True)
    x=s.add_parser('route-permit-report');x.add_argument('--db',required=True)
    d=s.add_parser('demo');d.add_argument('--db')
    a=p.parse_args()
    if a.cmd in COMMANDS:out=COMMANDS[a.cmd](a.db,a.file)
    elif a.cmd=='restore-decide':out=resident.decide(a.db,a.repair)
    elif a.cmd=='route-permit-decide':out=route_permit.decide(a.db,a.repair,a.at)
    elif a.cmd=='report':out=report(a.db)
    elif a.cmd=='audit':out=audit(a.db)
    elif a.cmd=='route-report':out=land.route_report(a.db)
    elif a.cmd=='route-permit-report':out=route_permit.permit_report(a.db)
    else:
        db=a.db or str(Path(tempfile.mkdtemp())/'demo.sqlite');f=Path(__file__).resolve().parent.parent/'fixtures'
        for cmd,name in [('segment-import','segment-ok.json'),('gauge-import','gauge-up-ok.json'),('gauge-import','gauge-down-ok.json'),('pressure-import','pressure-ok.json'),('valve-import','valve-ok.json'),('permit-import','permit-ok.json'),('repair-import','repair-ok.json'),('reopen-import','reopen-ok.json'),('notice-import','notice-ok.json'),('tap-import','tap-h1-ok.json'),('tap-import','tap-h2-ok.json')]:COMMANDS[cmd](db,f/name)
        out={'db':db,'restoration':resident.decide(db,'R1'),'audit':audit(db)}
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
