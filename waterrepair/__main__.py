import argparse,json,tempfile
from pathlib import Path
from . import pressure,repair,resident,operations,vibration,pump_health
from .report import report,audit
COMMANDS={'segment-import':pressure.segment,'gauge-import':pressure.gauge,'pressure-import':pressure.import_reading,
          'valve-import':repair.valve,'permit-import':repair.permit,'repair-import':repair.isolate,
          'reopen-import':repair.reopen,'notice-import':resident.notice,'tap-import':resident.tap,
          'pump-import':operations.pump,'tank-import':operations.tank,
          'pump-run-import':operations.run,'zone-demand-import':operations.demand,
          'vibration-import':vibration.sample,'tachometer-import':vibration.tach,
          'disposition-import':pump_health.disposition,'recapture-import':pump_health.recapture,
          'teardown-import':pump_health.teardown}
def main():
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='cmd',required=True)
    for name in COMMANDS:
        x=s.add_parser(name);x.add_argument('file');x.add_argument('--db',required=True)
    d=s.add_parser('restore-decide');d.add_argument('--db',required=True);d.add_argument('--repair',required=True)
    d=s.add_parser('health-assess');d.add_argument('--db',required=True);d.add_argument('--run',required=True)
    for name in ('report','audit'):
        x=s.add_parser(name);x.add_argument('--db',required=True)
    x=s.add_parser('operation-report');x.add_argument('--db',required=True)
    x=s.add_parser('vibration-report');x.add_argument('--db',required=True)
    x=s.add_parser('pump-health-report');x.add_argument('--db',required=True);x.add_argument('--run')
    d=s.add_parser('demo');d.add_argument('--db')
    a=p.parse_args()
    if a.cmd in COMMANDS:out=COMMANDS[a.cmd](a.db,a.file)
    elif a.cmd=='restore-decide':out=resident.decide(a.db,a.repair)
    elif a.cmd=='health-assess':out=pump_health.assess_decision(a.db,a.run)
    elif a.cmd=='report':out=report(a.db)
    elif a.cmd=='audit':out=audit(a.db)
    elif a.cmd=='operation-report':out=operations.operation_report(a.db)
    elif a.cmd=='vibration-report':out=vibration.captured(a.db)
    elif a.cmd=='pump-health-report':out=pump_health.health_report(a.db,a.run)
    else:
        db=a.db or str(Path(tempfile.mkdtemp())/'demo.sqlite');f=Path(__file__).resolve().parent.parent/'fixtures'
        for cmd,name in [('segment-import','segment-ok.json'),('gauge-import','gauge-up-ok.json'),('gauge-import','gauge-down-ok.json'),('pressure-import','pressure-ok.json'),('valve-import','valve-ok.json'),('permit-import','permit-ok.json'),('repair-import','repair-ok.json'),('reopen-import','reopen-ok.json'),('notice-import','notice-ok.json'),('tap-import','tap-h1-ok.json'),('tap-import','tap-h2-ok.json')]:COMMANDS[cmd](db,f/name)
        out={'db':db,'restoration':resident.decide(db,'R1'),'audit':audit(db)}
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
