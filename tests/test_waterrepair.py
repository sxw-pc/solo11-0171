import json,os,subprocess,sys,unittest,tempfile
from pathlib import Path
from waterrepair import pressure,repair,resident
from waterrepair.report import report,audit

FIX=Path(__file__).parent.parent/'fixtures'

class WaterRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=self.root/'state.sqlite'
    def tearDown(self):self.tmp.cleanup()
    def file(self,name,data):
        p=self.root/name;p.write_text(json.dumps(data),encoding='utf-8');return p
    def base(self):
        pressure.segment(self.db,FIX/'segment-ok.json')
        pressure.gauge(self.db,FIX/'gauge-up-ok.json')
        pressure.gauge(self.db,FIX/'gauge-down-ok.json')
    def leak(self):
        self.base();pressure.import_reading(self.db,FIX/'pressure-ok.json')
    def isolated(self):
        self.leak();repair.valve(self.db,FIX/'valve-ok.json');repair.permit(self.db,FIX/'permit-ok.json')
        repair.isolate(self.db,FIX/'repair-ok.json')
    def reopened(self):
        self.isolated();repair.reopen(self.db,FIX/'reopen-ok.json')

    def test_normal_restoration_and_compensation(self):
        print(__import__('waterrepair').__file__)
        self.reopened();resident.notice(self.db,FIX/'notice-ok.json')
        resident.tap(self.db,FIX/'tap-h1-ok.json');resident.tap(self.db,FIX/'tap-h2-ok.json')
        result=resident.decide(self.db,'R1')
        self.assertEqual(result['status'],'restored')
        self.assertEqual(result['compensation_minutes'],{'H1':40,'H2':40})
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_gauge_calibration_and_independence(self):
        self.base()
        doc=json.loads((FIX/'pressure-ok.json').read_text());doc['downstream_gauge']='G1'
        with self.assertRaisesRegex(ValueError,'independent_segment_gauges_required'):
            pressure.import_reading(self.db,self.file('same.json',doc))
        doc['downstream_gauge']='G2';doc['sampled_at']='2027-01-01T00:00:00Z'
        with self.assertRaisesRegex(ValueError,'gauge_calibration_expired'):
            pressure.import_reading(self.db,self.file('expired.json',doc))
        self.assertEqual(report(self.db)['state']['pressure'],[])

    def test_drop_and_late_revision_retract_repair(self):
        self.isolated()
        result=pressure.import_reading(self.db,FIX/'pressure-bad.json')
        self.assertEqual(result['status'],'clear')
        self.assertEqual(report(self.db)['state']['repair'][0]['status'],'disputed')
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_valve_topology_and_bypass(self):
        self.leak()
        short={'id':'V-short','segment_id':'S1','covered_households':['H1'],'bypass_closed':True}
        repair.valve(self.db,self.file('short.json',short));repair.permit(self.db,FIX/'permit-ok.json')
        doc=json.loads((FIX/'repair-ok.json').read_text());doc['valve_id']='V-short'
        with self.assertRaisesRegex(ValueError,'valve_coverage_incomplete'):
            repair.isolate(self.db,self.file('rshort.json',doc))
        bypass={**short,'id':'V-open','covered_households':['H1','H2'],'bypass_closed':False}
        repair.valve(self.db,self.file('open.json',bypass));doc['valve_id']='V-open'
        with self.assertRaisesRegex(ValueError,'bypass_open'):
            repair.isolate(self.db,self.file('ropen.json',doc))

    def test_crew_qualification_and_pressure_reopen(self):
        self.leak()
        bad=json.loads((FIX/'permit-ok.json').read_text());bad['max_diameter_mm']=100
        with self.assertRaisesRegex(ValueError,'crew_qualification_mismatch'):
            repair.permit(self.db,self.file('badpermit.json',bad))
        repair.valve(self.db,FIX/'valve-ok.json');repair.permit(self.db,FIX/'permit-ok.json')
        repair.isolate(self.db,FIX/'repair-ok.json')
        reopen=json.loads((FIX/'reopen-ok.json').read_text());reopen['test_kpa']=100
        with self.assertRaisesRegex(ValueError,'pressure_test_failed'):
            repair.reopen(self.db,self.file('low.json',reopen))
        self.assertEqual(report(self.db)['state']['repair'][0]['status'],'isolated')

    def test_notice_and_terminal_tap_independent(self):
        self.reopened()
        bad=json.loads((FIX/'notice-ok.json').read_text());bad['households']=['H1']
        with self.assertRaisesRegex(ValueError,'notice_households_incomplete'):
            resident.notice(self.db,self.file('partial.json',bad))
        self.assertEqual(resident.decide(self.db,'R1')['reason'],'notice_missing')
        resident.notice(self.db,FIX/'notice-ok.json')
        resident.tap(self.db,FIX/'tap-h1-ok.json')
        with self.assertRaisesRegex(ValueError,'independent_tap_witness_required'):
            resident.tap(self.db,FIX/'tap-bad.json')
        self.assertEqual(resident.decide(self.db,'R1')['reason'],'terminal_taps_missing')
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_replay_conflict_is_atomic(self):
        self.leak();self.assertTrue(pressure.import_reading(self.db,FIX/'pressure-ok.json')['replayed'])
        changed=json.loads((FIX/'pressure-ok.json').read_text());changed['downstream_kpa']=100
        with self.assertRaisesRegex(ValueError,'event_id_conflict'):
            pressure.import_reading(self.db,self.file('changed.json',changed))
        self.assertEqual(len(report(self.db)['state']['pressure']),1)

    def test_cli_and_module_origin(self):
        env={**os.environ,'PYTHONPATH':str(Path(__file__).parent.parent)}
        p=subprocess.run([sys.executable,'-m','waterrepair','demo','--db',str(self.root/'cli.sqlite')],env=env,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertTrue(json.loads(p.stdout)['audit']['replay_ok'])
        self.assertTrue(str(Path(__import__('waterrepair').__file__).resolve()).startswith(str(Path(__file__).parent.parent.resolve())))

if __name__=='__main__':unittest.main()
