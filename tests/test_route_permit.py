import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from waterrepair import pressure,repair,land,route_permit
from waterrepair.report import audit,report

FIX=Path(__file__).resolve().parents[1]/'fixtures'
AT='2026-07-01T06:00:00Z'

class RoutePermitTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=self.root/'state.sqlite'
    def tearDown(self):self.tmp.cleanup()
    def file(self,name,data):
        p=self.root/name;p.write_text(json.dumps(data),encoding='utf-8');return p
    def isolate_r1(self):
        pressure.segment(self.db,FIX/'segment-ok.json')
        pressure.gauge(self.db,FIX/'gauge-up-ok.json')
        pressure.gauge(self.db,FIX/'gauge-down-ok.json')
        pressure.import_reading(self.db,FIX/'pressure-route.json')
        repair.valve(self.db,FIX/'valve-ok.json')
        repair.permit(self.db,FIX/'permit-route.json')
        repair.isolate(self.db,FIX/'repair-route.json')
    def maps(self,corridors=('corridor-intrude.json','corridor-road.json','corridor-drill.json'),easement=True):
        land.parcel(self.db,FIX/'parcel-road.json')
        land.parcel(self.db,FIX/'parcel-court.json')
        route_permit.gas_line(self.db,FIX/'gas-line-ok.json')
        route_permit.method_spec(self.db,FIX/'method-spec-open-cut.json')
        route_permit.method_spec(self.db,FIX/'method-spec-drill.json')
        route_permit.closure(self.db,FIX/'closure-night.json')
        if easement:route_permit.easement(self.db,FIX/'easement-drill.json')
        land.worksite(self.db,FIX/'worksite-route.json')
        for c in corridors:land.corridor(self.db,FIX/c)
    def parcels_v2(self,precision_cm):
        for pid,owner,poly in [('P-ROAD','CITY',[[0,0],[20,0],[20,40],[0,40]]),
                               ('P-COURT','OWNER-LIN',[[20,0],[40,0],[40,40],[20,40]])]:
            land.parcel(self.db,self.file(f'{pid}-v2.json',{'id':pid,'owner':owner,'polygon':poly,
                'survey_version':'SURVEY-2027','crs':'GRID-2020','precision_cm':precision_cm,
                'valid_from':'2026-06-01T00:00:00Z'}))
    def late_gas(self):
        return self.file('gas-late.json',{'id':'GAS-LATE','path':[[15,25],[15,35]],'belt_cm':200,
            'crs':'GRID-2020','precision_cm':0,'survey_version':'SURVEY-2026','valid_from':'2026-06-01T00:00:00Z'})
    def routes(self,result):
        return {r['route_id']:r for w in result['worksites'] for r in w['routes']}
    def latest(self):
        permits=route_permit.permit_report(self.db)['permits']
        self.assertEqual(len(permits),1)
        return permits[0]
    def latest_routes(self):
        return {r['route_id']:r for r in self.latest()['detail']['routes']}

    def test_boundary_intrusion_and_two_methods(self):
        self.isolate_r1();self.maps()
        result=route_permit.decide(self.db,'R1',AT)
        self.assertFalse(result['replayed'])
        self.assertEqual(result['status'],'approved')
        routes=self.routes(result)
        # 中心线 x=19 落在道路内, 但 4m 开挖带实占 17~21, 侵入私有院落且压燃气安全带
        a=routes['C-A/open-cut']
        self.assertEqual(a['status'],'rejected')
        self.assertEqual({c['type'] for c in a['conflicts']},{'missing_right','gas_belt_crossing'})
        court=[o for o in a['occupies'] if o['parcel_id']=='P-COURT'][0]
        self.assertEqual(court['overlap'],'definite')
        self.assertTrue(court['intersections'])
        # 远离边界的合法路线不被邻近地块阻断: 夜间道路开挖 21:00 开工, 市政承担
        b=routes['C-B/open-cut']
        self.assertEqual(b['status'],'approved')
        self.assertEqual([o['parcel_id'] for o in b['occupies']],['P-ROAD'])
        self.assertEqual(b['earliest_start'],'2026-07-01T21:00:00Z')
        self.assertEqual(b['cost_responsibility'],'MUNICIPAL-EMERGENCY')
        self.assertEqual(b['authorities'],['ROAD-DEPT'])
        self.assertEqual(b['rights_chain'][0]['kind'],'closure')
        # 定向顶管穿越院落: 白天 08:00 开工, 按协议赔付占地与恢复费
        c=routes['C-C/directional-drill']
        self.assertEqual(c['status'],'approved')
        self.assertEqual(c['earliest_start'],'2026-07-01T08:00:00Z')
        self.assertEqual(c['cost_responsibility'],'PER-AGREEMENT')
        self.assertEqual(c['authorities'],['OWNER:OWNER-LIN'])
        self.assertEqual(c['rights_chain'][0]['compensation']['occupation_fee'],5000)
        self.assertEqual([o['parcel_id'] for o in c['occupies']],['P-COURT'])
        # 顶管路线不能走夜间道路许可, 也不能拿道路许可的方法开挖
        self.assertEqual(routes['C-C/open-cut']['status'],'rejected')
        self.assertEqual(routes['C-B/directional-drill']['status'],'rejected')
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_consent_method_binding(self):
        self.isolate_r1()
        self.maps(corridors=('corridor-intrude.json','corridor-drill.json','corridor-consent.json'),easement=False)
        land.consent(self.db,FIX/'land-consent-open-cut.json')
        routes=self.routes(route_permit.decide(self.db,'R1',AT))
        # open-cut 同意书 + 道路封闭许可共同支撑 C-D
        d=routes['C-D/open-cut']
        self.assertEqual(d['status'],'approved')
        self.assertEqual({r['kind'] for r in d['rights_chain']},{'closure','land_consent'})
        self.assertEqual(d['cost_responsibility'],'MIXED')
        self.assertEqual(d['earliest_start'],'2026-07-01T21:00:00Z')
        # 同一份 open-cut 同意书不能给定向顶管用
        c=routes['C-C/directional-drill']
        self.assertEqual(c['status'],'rejected')
        self.assertIn('missing_right',{x['type'] for x in c['conflicts']})
        # 侵入带虽有 open-cut 同意书, 仍被燃气安全带阻断
        a=routes['C-A/open-cut']
        self.assertEqual({x['type'] for x in a['conflicts']},{'gas_belt_crossing'})
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_crs_conflict(self):
        self.isolate_r1();self.maps()
        old=json.loads((FIX/'gas-line-ok.json').read_text());old['id']='GAS-OLD';old['crs']='GRID-1988'
        route_permit.gas_line(self.db,self.file('gas-old.json',old))
        with self.assertRaisesRegex(ValueError,'crs_conflict'):
            route_permit.decide(self.db,'R1',AT)

    def test_measurement_error_three_way(self):
        self.isolate_r1()
        self.maps(corridors=('corridor-intrude.json','corridor-road.json'),easement=False)
        self.parcels_v2(precision_cm=50)
        land.corridor(self.db,self.file('corridor-f.json',
            {'id':'C-F','worksite_id':'W-R1','path':[[17,10],[19,10],[19,5]],'width_cm':200}))
        routes=self.routes(route_permit.decide(self.db,'R1',AT))
        # 边界误差 ±0.5m: 开挖带 13~17 确定只在道路内 -> 确定可行
        b=routes['C-B/open-cut']
        self.assertEqual(b['status'],'approved')
        self.assertEqual([o['parcel_id'] for o in b['occupies']],['P-ROAD'])
        # 开挖带侵入内缩后的院落 -> 确定不可行
        a=routes['C-A/open-cut']
        self.assertEqual(a['status'],'rejected')
        self.assertEqual([o for o in a['occupies'] if o['parcel_id']=='P-COURT'][0]['overlap'],'definite')
        # 开挖带只与外扩后的院落相交 -> 待复测, 不选有利地图
        f=routes['C-F/open-cut']
        self.assertEqual(f['status'],'resurvey')
        self.assertEqual([o for o in f['occupies'] if o['parcel_id']=='P-COURT'][0]['overlap'],'uncertain')
        self.assertIn('uncertain_parcel',{x['type'] for x in f['resurvey']})

    def test_late_gas_map_readjudicates(self):
        self.isolate_r1();self.maps()
        route_permit.decide(self.db,'R1',AT)
        route_permit.gas_line(self.db,self.late_gas())
        permit=self.latest()
        self.assertEqual(permit['seq'],2)
        self.assertEqual(permit['status'],'approved')
        routes=self.latest_routes()
        b=routes['C-B/open-cut']
        self.assertEqual(b['status'],'rejected')
        self.assertIn('gas_belt_crossing',{c['type'] for c in b['conflicts']})
        self.assertEqual(routes['C-C/directional-drill']['status'],'approved')
        self.assertEqual(permit['detail']['alternatives'],['C-C/directional-drill'])
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_withdrawn_when_no_alternative(self):
        self.isolate_r1()
        self.maps(corridors=('corridor-road.json',),easement=False)
        route_permit.decide(self.db,'R1',AT)
        route_permit.gas_line(self.db,self.late_gas())
        permit=self.latest()
        self.assertEqual(permit['status'],'withdrawn')
        self.assertEqual(permit['detail']['alternatives'],[])
        conflicts={c['type'] for r in permit['detail']['routes'] for c in r['conflicts']}
        self.assertIn('gas_belt_crossing',conflicts)
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_started_permit_survives_late_gas(self):
        self.isolate_r1();self.maps()
        route_permit.decide(self.db,'R1',AT)
        early={'id':'RS-0','worksite_id':'W-R1','corridor_id':'C-B','method':'open-cut','at':'2026-07-01T20:00:00Z'}
        with self.assertRaisesRegex(ValueError,'start_before_window'):
            route_permit.start(self.db,self.file('start-early.json',early))
        route_permit.start(self.db,self.file('start-ok.json',
            {'id':'RS-1','worksite_id':'W-R1','corridor_id':'C-B','method':'open-cut','at':'2026-07-01T21:30:00Z'}))
        route_permit.gas_line(self.db,self.late_gas())
        permit=self.latest()
        self.assertEqual(permit['seq'],1)
        self.assertEqual(permit['status'],'approved')
        starts=route_permit.permit_report(self.db)['starts']
        self.assertEqual([s['id'] for s in starts],['RS-1'])
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_shared_closure_quota(self):
        self.isolate_r1()
        self.maps(corridors=('corridor-road.json',),easement=False)
        self.assertEqual(route_permit.decide(self.db,'R1',AT)['status'],'approved')
        pressure.segment(self.db,self.file('s2.json',{'id':'S2','material':'iron','diameter_mm':300,'households':['H3']}))
        pressure.gauge(self.db,self.file('g3.json',{'id':'G3','segment_id':'S2','position':'upstream','calibrated_until':'2026-12-01T00:00:00Z'}))
        pressure.gauge(self.db,self.file('g4.json',{'id':'G4','segment_id':'S2','position':'downstream','calibrated_until':'2026-12-01T00:00:00Z'}))
        pressure.import_reading(self.db,self.file('p2.json',{'id':'P2','segment_id':'S2','upstream_gauge':'G3','downstream_gauge':'G4',
            'upstream_kpa':260,'downstream_kpa':190,'sampled_at':'2026-07-01T05:00:00Z','revision':1}))
        repair.valve(self.db,self.file('v2.json',{'id':'V2','segment_id':'S2','covered_households':['H3'],'bypass_closed':True}))
        repair.permit(self.db,self.file('per2.json',{'id':'PER2','segment_id':'S2','crew':'CREW1','material':'iron','max_diameter_mm':350,'valid_to':'2026-07-10T00:00:00Z'}))
        repair.isolate(self.db,self.file('r2.json',{'id':'R2','segment_id':'S2','valve_id':'V2','permit_id':'PER2','started_at':'2026-07-01T05:30:00Z'}))
        land.worksite(self.db,self.file('w2.json',{'id':'W-R2','repair_id':'R2','x':17,'y':4,'method':'open-cut'}))
        land.corridor(self.db,self.file('cg.json',{'id':'C-G','worksite_id':'W-R2','path':[[17,4],[15,4],[15,8]],'width_cm':400}))
        result=route_permit.decide(self.db,'R2',AT)
        self.assertEqual(result['status'],'rejected')
        g=self.routes(result)['C-G/open-cut']
        quota=[c for c in g['conflicts'] if c['type']=='closure_quota_exhausted']
        self.assertEqual(quota[0]['closure_id'],'CL-1')
        self.assertEqual(quota[0]['users'],['W-R1'])
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_easement_withdrawal(self):
        self.isolate_r1();self.maps()
        route_permit.decide(self.db,'R1',AT)
        route_permit.easement_withdraw(self.db,self.file('wd.json',
            {'id':'WD-1','easement_id':'EA-1','withdrawn_at':'2026-07-01T12:00:00Z'}))
        routes=self.latest_routes()
        c=routes['C-C/directional-drill']
        self.assertEqual(c['status'],'rejected')
        self.assertIn('missing_right',{x['type'] for x in c['conflicts']})
        self.assertEqual(routes['C-B/open-cut']['status'],'approved')
        self.assertEqual(report(self.db)['state']['easement'][0]['status'],'withdrawn')
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_survey_revoke_and_new_version(self):
        self.isolate_r1();self.maps()
        route_permit.decide(self.db,'R1',AT)
        route_permit.survey_revoke(self.db,self.file('sr.json',
            {'id':'SR-1','survey_version':'SURVEY-2026','revoked_at':'2026-07-01T12:00:00Z'}))
        permit=self.latest()
        self.assertEqual(permit['status'],'resurvey')
        flags={x['type'] for r in permit['detail']['routes'] for x in r['resurvey']}
        self.assertIn('cadastre_unavailable',flags)
        self.parcels_v2(precision_cm=0)
        permit=self.latest()
        self.assertEqual(permit['status'],'approved')
        routes={r['route_id']:r for r in permit['detail']['routes']}
        self.assertEqual(routes['C-B/open-cut']['status'],'approved')
        self.assertEqual(routes['C-C/directional-drill']['status'],'approved')
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_obstacle_blocks_route(self):
        self.isolate_r1();self.maps()
        route_permit.obstacle(self.db,self.file('obs.json',
            {'id':'OBS-1','polygon':[[14,20],[16,20],[16,22],[14,22]],'crs':'GRID-2020','precision_cm':0,
             'survey_version':'SURVEY-2026','valid_from':'2026-01-01T00:00:00Z'}))
        routes=self.routes(route_permit.decide(self.db,'R1',AT))
        b=routes['C-B/open-cut']
        self.assertEqual(b['status'],'rejected')
        self.assertIn('obstacle_intrusion',{c['type'] for c in b['conflicts']})
        self.assertEqual(routes['C-C/directional-drill']['status'],'approved')

    def test_idempotency_and_conflict(self):
        self.isolate_r1();self.maps()
        route_permit.decide(self.db,'R1',AT)
        self.assertTrue(route_permit.decide(self.db,'R1',AT)['replayed'])
        self.assertTrue(route_permit.easement(self.db,FIX/'easement-drill.json')['replayed'])
        bad=json.loads((FIX/'easement-drill.json').read_text());bad['compensation']['occupation_fee']=9999
        with self.assertRaisesRegex(ValueError,'event_id_conflict'):
            route_permit.easement(self.db,self.file('ea-bad.json',bad))
        gas=json.loads((FIX/'gas-line-ok.json').read_text());gas['belt_cm']=300
        with self.assertRaisesRegex(ValueError,'event_id_conflict'):
            route_permit.gas_line(self.db,self.file('gas-bad.json',gas))
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_decide_requires_isolation(self):
        self.isolate_r1();self.maps()
        repair.reopen(self.db,FIX/'reopen-ok.json')
        with self.assertRaisesRegex(ValueError,'repair_not_isolated'):
            route_permit.decide(self.db,'R1',AT)
        with self.assertRaisesRegex(ValueError,'unknown_repair'):
            route_permit.decide(self.db,'R-X',AT)

    def test_cli(self):
        env={**os.environ,'PYTHONPATH':str(Path(__file__).parent.parent)}
        db=self.root/'cli.sqlite'
        steps=[('segment-import','segment-ok.json'),('gauge-import','gauge-up-ok.json'),('gauge-import','gauge-down-ok.json'),
               ('pressure-import','pressure-route.json'),('valve-import','valve-ok.json'),('permit-import','permit-route.json'),
               ('repair-import','repair-route.json'),('worksite-import','worksite-route.json'),
               ('parcel-import','parcel-road.json'),('parcel-import','parcel-court.json'),
               ('gas-line-import','gas-line-ok.json'),('method-spec-import','method-spec-open-cut.json'),
               ('method-spec-import','method-spec-drill.json'),('closure-import','closure-night.json'),
               ('easement-import','easement-drill.json'),('corridor-import','corridor-intrude.json'),
               ('corridor-import','corridor-road.json'),('corridor-import','corridor-drill.json')]
        for cmd,name in steps:
            p=subprocess.run([sys.executable,'-m','waterrepair',cmd,str(FIX/name),'--db',str(db)],env=env,capture_output=True,text=True)
            self.assertEqual(p.returncode,0,f'{cmd}: {p.stderr}')
        p=subprocess.run([sys.executable,'-m','waterrepair','route-permit-decide','--db',str(db),'--repair','R1','--at',AT],
                         env=env,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(json.loads(p.stdout)['status'],'approved')
        p=subprocess.run([sys.executable,'-m','waterrepair','route-permit-report','--db',str(db)],env=env,capture_output=True,text=True)
        self.assertEqual(json.loads(p.stdout)['permits'][0]['status'],'approved')
        p=subprocess.run([sys.executable,'-m','waterrepair','audit','--db',str(db)],env=env,capture_output=True,text=True)
        self.assertTrue(json.loads(p.stdout)['replay_ok'])

if __name__=='__main__':unittest.main()
