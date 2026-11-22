import json,subprocess,sys,tempfile,unittest
from pathlib import Path
from waterrepair import operations,performance,pump_acceptance
from waterrepair.report import audit,report

FIX=Path(__file__).resolve().parents[1]/'fixtures'

def base_chain(db):
    operations.pump(db,FIX/'pump-ok.json')
    operations.run(db,FIX/'pump-run-ok.json')
    operations.run(db,FIX/'pump-run-after.json')
    performance.test_point(db,FIX/'pump-test-before.json')
    performance.test_point(db,FIX/'pump-test-after.json')
    pump_acceptance.case(db,FIX/'acceptance-case-ok.json')
    for f in ('meter-energy-bus-ok.json','meter-flow-ok.json','meter-head-ok.json','meter-speed-ok.json'):
        pump_acceptance.meter(db,FIX/f)
    pump_acceptance.bus_share(db,FIX/'bus-allocation-before-ok.json')
    pump_acceptance.bus_share(db,FIX/'bus-allocation-after-ok.json')
    for f in ('evidence-pt1-energy-ok.json','evidence-pt1-flow-ok.json','evidence-pt1-head-ok.json','evidence-pt1-speed-ok.json',
              'evidence-pt2-energy-ok.json','evidence-pt2-flow-ok.json','evidence-pt2-head-ok.json'):
        pump_acceptance.evidence(db,FIX/f)

class PumpAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Path(self.tmp.name)/'state.sqlite'
    def tearDown(self):self.tmp.cleanup()
    def file(self,name,data):
        p=Path(self.tmp.name)/name;p.write_text(json.dumps(data),encoding='utf-8');return p

    def test_envelope_holds_both_curves_and_signs_nothing(self):
        base_chain(self.db)
        ev=pump_acceptance.evaluate(self.db,'AC1')
        self.assertEqual(ev['factory_guarantee'],'indeterminate')
        self.assertEqual(ev['reference_envelope_wh']['lower_wh'],25000.0)
        self.assertEqual(ev['reference_envelope_wh']['upper_wh'],28800.0)
        # The 25500 Wh guarantee sits inside the feasible envelope: no single
        # point formula may emit either a pass or a fail device state.
        self.assertTrue(ev['reference_envelope_wh']['lower_wh']<=25500<ev['reference_envelope_wh']['upper_wh'])
        certs=pump_acceptance.certificates(self.db)
        self.assertEqual(certs,[])
        # Naive Wh/L alone would claim 20% saving; the raw ratio must not be
        # used as a reference-condition conclusion anywhere.
        self.assertAlmostEqual((30000-24000)/30000,0.2)

    def test_supplemental_plan_targets_safe_head_with_two_outcomes(self):
        base_chain(self.db)
        ev=pump_acceptance.evaluate(self.db,'AC1')
        plan=ev['supplemental_plan']
        self.assertEqual(plan['recommended_head_kpa'],295)
        self.assertLess(plan['pass_if_energy_wh_at_most'],25500)
        # The two decision edges straddle the guarantee and leave a pending
        # band: readings between them stay undecided rather than forcing a
        # certificate by averaging or naive scaling.
        self.assertEqual(plan['pending_band_wh'],[25075.0,25400.0])
        # Curve 甲 (25000 Wh at 295 kPa) clears the pass edge; curve 乙
        # (28000 Wh) crosses the fail edge — one input, opposite verdicts.
        self.assertLessEqual(25000,plan['pass_if_energy_wh_at_most'])
        self.assertGreater(28000,plan['fail_if_energy_wh_above'])

    def _supplemental_point(self,tag,run_id,measured,head,energy):
        run={'id':run_id,'pump_id':'P1','started_at':f'2026-03-10T08:00:00Z','stopped_at':'2026-03-10T09:00:00Z','volume_l':72000,'energy_wh':energy}
        operations.run(self.db,self.file(run_id+'.json',run))
        test={'id':tag,'pump_id':'P1','run_id':run_id,'phase':'after','head_kpa':head,'volume_l':72000,'energy_wh':energy,'measured_at':measured}
        performance.test_point(self.db,self.file(tag+'.json',test))
        ba={'id':tag+'BA','meter_id':'ME1','pump_id':'P1','share':0.8,
            'window_start':'2026-03-10T08:00:00Z','window_end':'2026-03-10T09:00:00Z'}
        pump_acceptance.bus_share(self.db,self.file(tag+'ba.json',ba))
        for kind,meter,val,unit in (('energy','ME1',energy/0.8,'Wh'),('flow','MF1',72000.0,'L'),('head','MH1',float(head),'kPa')):
            pump_acceptance.evidence(self.db,self.file(tag+kind+'.json',
                {'id':tag+kind.upper(),'test_id':tag,'meter_id':meter,'meter_kind':kind,'reading_value':val,'unit':unit,
                 'stable_window_start':'2026-03-10T08:05:00Z','stable_window_end':'2026-03-10T08:55:00Z',
                 'startup_excluded':True,'parallel_pumps':['P2'],'speed_hz':50}))

    def test_same_250kpa_input_splits_into_opposite_curve_outcomes(self):
        # Both physical devices share the identical 250 kPa / 24000 Wh point.
        base_chain(self.db)
        self._supplemental_point('PT3A','PR3A','2026-03-10T08:30:00Z',295,25000)  # curve 甲
        ev_a=pump_acceptance.evaluate(self.db,'AC1')
        self.assertEqual(ev_a['factory_guarantee'],'pass')
        self.assertLessEqual(ev_a['reference_envelope_wh']['upper_wh'],25500)
        # Fresh ledger for curve 乙: same original 250 kPa evidence, different
        # second after-retrofit head point.
        db2=Path(self.tmp.name)/'curve_b.sqlite'
        base_chain(db2)
        # Rebind the helper to the second ledger.
        old=self.db;self.db=db2
        try:self._supplemental_point('PT3B','PR3B','2026-03-10T08:30:00Z',295,28000)  # curve 乙
        finally:self.db=old
        ev_b=pump_acceptance.evaluate(db2,'AC1')
        self.assertEqual(ev_b['factory_guarantee'],'fail')
        self.assertGreater(ev_b['reference_envelope_wh']['lower_wh'],25500)

    def test_two_authorities_issue_distinct_texts_and_todos(self):
        base_chain(self.db)
        out=pump_acceptance.publish(self.db,'AC1')
        self.assertEqual(out['factory_guarantee'],'indeterminate')
        certs={c['authority']:c for c in pump_acceptance.certificates(self.db)}
        self.assertEqual(certs['manufacturer']['status'],'trial')
        self.assertIn('TRIAL OPERATION ONLY',certs['manufacturer']['cert_text'])
        self.assertIn('supplemental test',certs['manufacturer']['cert_text'])
        self.assertEqual(certs['municipal_field']['status'],'limited_field')
        self.assertIn('20.0%',certs['municipal_field']['cert_text'])
        self.assertIn('not the manufacturer',certs['municipal_field']['cert_text'])
        cond=json.loads(certs['municipal_field']['condition_json'])
        self.assertEqual(cond['field_saving_ratio'],0.2)
        # Re-publish is idempotent: no duplicate certificate versions.
        pump_acceptance.publish(self.db,'AC1')
        rows=pump_acceptance.certificates(self.db)
        self.assertEqual(len(rows),2)

    def test_formal_upgrade_replaces_trial_with_version_history(self):
        base_chain(self.db)
        pump_acceptance.publish(self.db,'AC1')
        self._supplemental_point('PT3A','PR3A','2026-03-10T08:30:00Z',295,25000)
        out=pump_acceptance.publish(self.db,'AC1')
        self.assertEqual(out['factory_guarantee'],'pass')
        manu=[c for c in pump_acceptance.certificates(self.db) if c['authority']=='manufacturer']
        self.assertEqual([c['version'] for c in manu],[1,2])
        self.assertEqual([c['status'] for c in manu],['trial','formal'])
        self.assertEqual(manu[1]['supersedes_id'],manu[0]['id'])
        self.assertIn('FORMAL PERFORMANCE CERTIFICATE',manu[1]['cert_text'])

    def test_shared_bus_allocation_is_required_and_split(self):
        base_chain(self.db)
        db2=Path(self.tmp.name)/'no_alloc.sqlite'
        operations.pump(db2,FIX/'pump-ok.json')
        operations.run(db2,FIX/'pump-run-ok.json')
        performance.test_point(db2,FIX/'pump-test-before.json')
        pump_acceptance.case(db2,FIX/'acceptance-case-ok.json')
        pump_acceptance.meter(db2,FIX/'meter-energy-bus-ok.json')
        bad={'id':'X','test_id':'PT1','meter_id':'ME1','meter_kind':'energy','reading_value':37500.0,'unit':'Wh',
             'stable_window_start':'2026-01-10T08:05:00Z','stable_window_end':'2026-01-10T08:55:00Z',
             'startup_excluded':True,'parallel_pumps':['P2']}
        with self.assertRaisesRegex(ValueError,'missing_bus_allocation'):
            pump_acceptance.evidence(db2,self.file('bad.json',bad))
        # A reading whose P1 share does not equal the test energy is rejected:
        # other busbar pumps / VFD losses cannot be billed to the tested pump.
        bad['reading_value']=99999.0
        with self.assertRaisesRegex(ValueError,'bus_allocation_mismatch'):
            pump_acceptance.evidence(self.db,self.file('bad2.json',bad))

    def test_transient_and_stable_window_are_guarded(self):
        base_chain(self.db)
        bad=json.loads((FIX/'evidence-pt2-energy-ok.json').read_text());bad['id']='BAD';bad['startup_excluded']=False
        with self.assertRaisesRegex(ValueError,'startup_transient_not_excluded'):
            pump_acceptance.evidence(self.db,self.file('bad.json',bad))
        bad=json.loads((FIX/'evidence-pt2-energy-ok.json').read_text());bad['id']='BAD2'
        bad['stable_window_start']='2026-02-10T07:30:00Z'
        with self.assertRaisesRegex(ValueError,'window_outside_run'):
            pump_acceptance.evidence(self.db,self.file('bad2.json',bad))

    def test_calibration_correction_withdraws_certificates_but_keeps_history(self):
        base_chain(self.db)
        pump_acceptance.publish(self.db,'AC1')
        before=pump_acceptance.certificates(self.db)
        res=pump_acceptance.calibration(self.db,FIX/'calibration-correction-bad.json')
        self.assertEqual(len(res['withdrawn']),2)
        after={c['id']:c for c in pump_acceptance.certificates(self.db)}
        for cid in ('W-P1-manufacturer','W-P1-municipal_field'):
            self.assertEqual(after[cid]['status'],'withdrawn')
            self.assertIn('WITHDRAWN',after[cid]['cert_text'])
        # Original certificate versions stay on record; nothing is deleted.
        for c in before:self.assertIn(c['id'],after)
        ev=pump_acceptance.evaluate(self.db,'AC1')
        self.assertTrue(any(i['reason']=='meter_calibration_expired' for i in ev['evidence_issues']))
        # Re-applying the same correction is a replay, not a second withdrawal.
        again=pump_acceptance.calibration(self.db,FIX/'calibration-correction-bad.json')
        self.assertTrue(again['replayed'])
        self.assertEqual(len([c for c in pump_acceptance.certificates(self.db) if c['status']=='withdrawn']),2)

    def test_published_and_withdrawn_chain_replays_bit_identically(self):
        base_chain(self.db)
        pump_acceptance.publish(self.db,'AC1')
        pump_acceptance.calibration(self.db,FIX/'calibration-correction-bad.json')
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_republish_after_withdrawal_does_not_duplicate_versions(self):
        base_chain(self.db)
        pump_acceptance.publish(self.db,'AC1')
        pump_acceptance.calibration(self.db,FIX/'calibration-correction-bad.json')
        n=len(pump_acceptance.certificates(self.db))
        # A later publish after withdrawal must recognize the retracted chain
        # and stay idempotent rather than minting parallel new versions.
        pump_acceptance.publish(self.db,'AC1')
        pump_acceptance.publish(self.db,'AC1')
        self.assertEqual(len(pump_acceptance.certificates(self.db)),n)
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_non_invalidating_correction_keeps_certificates_live(self):
        base_chain(self.db)
        pump_acceptance.publish(self.db,'AC1')
        ok={'id':'CC2','meter_id':'ME1','corrected_at':'2026-03-02T00:00:00Z',
            'previous_calibrated_until':'2027-12-31T00:00:00Z','new_calibrated_until':'2028-06-30T00:00:00Z',
            'invalidates_readings':False,'reason':'routine_recertification'}
        res=pump_acceptance.calibration(self.db,self.file('cc2.json',ok))
        self.assertEqual(res['withdrawn'],[])
        statuses={c['id']:c['status'] for c in pump_acceptance.certificates(self.db)}
        self.assertEqual(statuses['C-P1-manufacturer-v1'],'trial')
        self.assertEqual(statuses['C-P1-municipal_field-v1'],'limited_field')
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_formal_curve_chain_replays(self):
        base_chain(self.db)
        self._supplemental_point('PT3A','PR3A','2026-03-10T08:30:00Z',295,25000)
        pump_acceptance.publish(self.db,'AC1')
        self.assertTrue(audit(self.db)['replay_ok'])

    def test_hydraulic_floor_guard(self):
        with self.assertRaisesRegex(ValueError,'energy_below_hydraulic_floor'):
            pump_acceptance.point_bounds({'id':'Z','head_kpa':300,'energy_wh':100,'volume_l':72000},300)
        with self.assertRaisesRegex(ValueError,'curve_points_inconsistent'):
            pump_acceptance.reference_envelope(
                [{'id':'A','head_kpa':250,'energy_wh':24000,'volume_l':72000},
                 {'id':'B','head_kpa':290,'energy_wh':99999,'volume_l':72000}],300)

    def test_cli_acceptance_report_and_publish(self):
        base_chain(self.db)
        env={**dict(__import__('os').environ),'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
        p=subprocess.run([sys.executable,'-m','waterrepair','acceptance-publish','--db',str(self.db),'--case','AC1'],
                         env=env,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        out=json.loads(p.stdout)
        self.assertEqual(out['factory_guarantee'],'indeterminate')
        p=subprocess.run([sys.executable,'-m','waterrepair','acceptance-report','--db',str(self.db)],
                         env=env,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        rep=json.loads(p.stdout)
        self.assertEqual(rep['cases'][0]['factory_guarantee'],'indeterminate')
        self.assertEqual(len(rep['certificates']),2)

if __name__=='__main__':unittest.main()
