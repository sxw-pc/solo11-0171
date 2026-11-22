import json,math,tempfile,unittest
from pathlib import Path
from waterrepair import operations,vibration,pump_health
from waterrepair.report import audit,report

FIX=Path(__file__).resolve().parents[1]/'fixtures'

def sin_wave(freq,fs,n,phase=0.0,amp=1.0):
    # 与采录台一致按 9 位小数存值；两源在该精度下逐点完全相同
    return [round(amp*math.sin(2*math.pi*freq*k/fs+phase),9) for k in range(n)]

class PumpHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=self.root/'state.sqlite'
    def tearDown(self):self.tmp.cleanup()
    def file(self,name,data):
        p=self.root/name;p.write_text(json.dumps(data),encoding='utf-8');return p
    def add_pump(self,pump_id='P1',station='EAST',flow=20,power=30):
        operations.pump(self.db,self.file(f'{pump_id}.json',
            {'id':pump_id,'station':station,'flow_lps':flow,'power_kw':power}))
    def add_run(self,run_id='PR1',pump_id='P1',volume=72000,energy=30000,
            start='2026-01-10T08:00:00Z',stop='2026-01-10T09:00:00Z'):
        operations.run(self.db,self.file(f'{run_id}.json',
            {'id':run_id,'pump_id':pump_id,'started_at':start,'stopped_at':stop,
             'volume_l':volume,'energy_wh':energy}))
    def add_sample(self,sid,run_id,pump_id,fs,axis,values,when='2026-01-10T08:30:00Z'):
        vibration.sample(self.db,self.file(f'{sid}.json',
            {'id':sid,'pump_id':pump_id,'run_id':run_id,'sample_rate_hz':fs,
             'axis':axis,'values':values,'recorded_at':when}))
    def add_tach(self,tid,run_id,pump_id,rpm=1200,when='2026-01-10T08:30:00Z'):
        vibration.tach(self.db,self.file(f'{tid}.json',
            {'id':tid,'pump_id':pump_id,'run_id':run_id,'rpm':rpm,'recorded_at':when}))
    def candidate_key(self,a):
        return sorted((c['fault'],c['source_hz'],c['status']) for c in a['candidates'])

    # 1. 同一低采样波形对应两种不同高频原波形，单段不可辨识
    def test_two_physical_waveforms_are_indistinguishable(self):
        fs,n=200,200
        # 物理源 A：健康但轻微不平衡的 20Hz 原波形
        wave_a=sin_wave(20,fs,n)
        # 物理源 B：180Hz 轴承损伤分量，相位 π
        wave_b=sin_wave(180,fs,n,phase=math.pi)
        # 两个高频原波形在每个 200Hz 采样点数值完全相同
        self.assertEqual(wave_a,wave_b)
        self.add_pump();self.add_run('PR1');self.add_sample('VS-A','PR1','P1',fs,'radial',wave_a);self.add_tach('T-A','PR1','P1')
        self.add_run('PR2');self.add_sample('VS-B','PR2','P1',fs,'radial',wave_b);self.add_tach('T-B','PR2','P1')
        a1=pump_health.assess_decision(self.db,'PR1')
        a2=pump_health.assess_decision(self.db,'PR2')
        # 同一份输入 -> 同一个判别结论
        self.assertEqual(a1['status'],'indeterminate');self.assertEqual(a2['status'],'indeterminate')
        self.assertEqual(self.candidate_key(a1),self.candidate_key(a2))
        faults={c['fault'] for c in a1['candidates']}
        self.assertEqual(faults,{'unbalance_1x','bearing_impact_aliased'})
        # 检修对象不同：转子平衡 vs 轴承，单段波形不可辨识
        self.assertEqual({c['maintenance_target'] for c in a1['candidates']},
                         {'rotor_balance','bearing'})
        bearing=[c for c in a1['candidates'] if c['fault']=='bearing_impact_aliased']
        self.assertEqual({c['source_hz'] for c in bearing},{180.0})
        # 两个候选都保持 indistinguishable，绝不标定唯一故障/零件
        self.assertTrue(all(c['status']=='indistinguishable' for c in a1['candidates']))
        # 可证集合只含谱线与相干性观察，不含“轴承正常”之类结论
        self.assertEqual({p['kind'] for p in a1['provable']},
                         {'spectral_line','tach_coherent_order1'})
        # 混叠等价类显式保留两个物理源
        equiv=a1['alias_class'][0]['equivalent_source_hz']
        self.assertIn(20.0,equiv);self.assertIn(180.0,equiv)
        self.assertTrue(audit(self.db)['replay_ok'])

    # 2. 可停机站点：拆检工单 + 从备机名册移走，不宣称唯一故障
    def test_shutdown_station_teardown_and_roster_removal(self):
        fs,n=200,200
        self.add_pump();self.add_run();self.add_sample('VS1','PR1','P1',fs,'radial',sin_wave(20,fs,n));self.add_tach('TACH1','PR1','P1')
        d={'id':'WO1','pump_id':'P1','run_id':'PR1','station':'shutdown','issued_at':'2026-01-10T10:00:00Z'}
        out=pump_health.disposition(self.db,self.file('d1.json',d))
        self.assertEqual(out['status'],'shutdown_teardown');self.assertEqual(out['standby_capacity_lps'],0)
        rep=pump_health.health_report(self.db)
        wo=rep['work_orders'][0]
        self.assertEqual(wo['kind'],'teardown_inspection');self.assertEqual(wo['status'],'open')
        self.assertEqual(rep['standby_roster'][0]['mode'],'removed')
        self.assertEqual(rep['committed_capacity_lps_by_station'],{})
        self.assertTrue(all(c['status']=='indistinguishable'
                            for c in rep['assessments'][0]['candidates']))
        self.assertTrue(audit(self.db)['replay_ok'])

    # 3. 医院应急站点：签章后限速轮值 + 1kHz 双轴复采任务，容量只按限速计入
    def test_hospital_limited_duty_requires_signature_and_opens_recapture(self):
        fs,n=200,200
        self.add_pump();self.add_run();self.add_sample('VS1','PR1','P1',fs,'radial',sin_wave(20,fs,n));self.add_tach('TACH1','PR1','P1')
        d={'id':'WO2','pump_id':'P1','run_id':'PR1','station':'hospital','issued_at':'2026-01-10T10:00:00Z'}
        with self.assertRaisesRegex(ValueError,'mechanical_supervisor_signature_required'):
            pump_health.disposition(self.db,self.file('d2.json',d))
        d['signed_by']='mech_supervisor_chen'
        out=pump_health.disposition(self.db,self.file('d2s.json',d))
        self.assertEqual(out['status'],'limited_duty')
        self.assertEqual(out['speed_limit_rpm'],960)          # 1200 的 80%
        self.assertEqual(out['standby_capacity_lps'],16)      # 20lps * 960/1200
        rep=pump_health.health_report(self.db)
        self.assertEqual(rep['committed_capacity_lps_by_station'],{'hospital':16})
        task=rep['recapture_tasks'][0]
        self.assertEqual(task['target_sample_rate_hz'],1000)
        self.assertEqual(task['axes'],['radial','axial'])
        self.assertEqual(task['anti_alias_filter'],1);self.assertEqual(task['sync_tach_pulse'],1)
        self.assertEqual(task['status'],'open')
        self.assertTrue(audit(self.db)['replay_ok'])

    # 4. 同一份 200Hz 原件，两种处置：一个实际停机、一个有条件运行，承诺容量不同
    def test_same_evidence_two_dispositions_diverge(self):
        fs,n=200,200
        results={}
        for station in ('shutdown','hospital'):
            db=self.root/f'{station}.sqlite'
            (self.root/f'p-{station}.json').write_text(json.dumps(
                {'id':'P1','station':'EAST','flow_lps':20,'power_kw':30}))
            operations.pump(db,self.root/f'p-{station}.json')
            (self.root/f'r-{station}.json').write_text(json.dumps(
                {'id':'PR1','pump_id':'P1','started_at':'2026-01-10T08:00:00Z',
                 'stopped_at':'2026-01-10T09:00:00Z','volume_l':72000,'energy_wh':30000}))
            operations.run(db,self.root/f'r-{station}.json')
            (self.root/f's-{station}.json').write_text(json.dumps(
                {'id':'VS1','pump_id':'P1','run_id':'PR1','sample_rate_hz':fs,'axis':'radial',
                 'values':sin_wave(20,fs,n),'recorded_at':'2026-01-10T08:30:00Z'}))
            vibration.sample(db,self.root/f's-{station}.json')
            (self.root/f't-{station}.json').write_text(json.dumps(
                {'id':'TACH1','pump_id':'P1','run_id':'PR1','rpm':1200,'recorded_at':'2026-01-10T08:30:00Z'}))
            vibration.tach(db,self.root/f't-{station}.json')
            d={'id':'W','pump_id':'P1','run_id':'PR1','station':station,'issued_at':'2026-01-10T10:00:00Z'}
            if station=='hospital':d['signed_by']='mech_supervisor_chen'
            f=self.root/f'd-{station}.json';f.write_text(json.dumps(d))
            results[station]=pump_health.disposition(db,f)
        self.assertEqual(results['shutdown']['standby_capacity_lps'],0)
        self.assertEqual(results['hospital']['standby_capacity_lps'],16)
        self.assertEqual(results['shutdown']['assessment_status'],'indeterminate')
        self.assertEqual(results['hospital']['assessment_status'],'indeterminate')

    # 5. 1kHz 复采证实 180Hz 冲击 -> 轴承更换单；已执行的停机/拆检历史保留
    def test_recapture_confirms_bearing_and_history_is_retained(self):
        fs,n=200,200
        self.add_pump();self.add_run();self.add_sample('VS1','PR1','P1',fs,'radial',sin_wave(20,fs,n));self.add_tach('TACH1','PR1','P1')
        pump_health.disposition(self.db,self.file('d.json',
            {'id':'WO1','pump_id':'P1','run_id':'PR1','station':'shutdown','issued_at':'2026-01-10T10:00:00Z'}))
        # 拆检找到滚道损伤 -> 标定轴承故障并开出更换单
        pump_health.teardown(self.db,self.file('td.json',
            {'id':'TD1','work_order_id':'WO1','finding':'raceway_damage','observed_at':'2026-01-11T09:00:00Z'}))
        rep=pump_health.health_report(self.db)
        self.assertEqual({w['kind']:w['status'] for w in rep['work_orders']},
                         {'teardown_inspection':'completed_confirmed','bearing_replacement':'open'})
        self.assertEqual(rep['teardown_findings'][0]['confirmed_fault'],'bearing_raceway_damage')
        # 1kHz 双轴抗混叠复采，径向含 180Hz 冲击
        hi=1000;N=2000
        radial=[round(math.sin(2*math.pi*20*k/hi)+0.5*math.sin(2*math.pi*180*k/hi),9) for k in range(N)]
        axial=sin_wave(20,hi,N)
        out=self._recapture('RC1',radial,axial)
        self.assertEqual(out['verdict'],'bearing_confirmed')
        self.assertTrue(out['bearing_hits'])
        self.assertEqual(out['assessment_status'],'bearing_confirmed')
        # 等价源已消解：轴承候选 confirmed，不再是 indistinguishable
        rep=pump_health.health_report(self.db)
        a=rep['assessments'][0]
        self.assertTrue(any(c['fault']=='bearing_impact_aliased' and c['status']=='confirmed' for c in a['candidates']))
        kinds={w['kind']:w['status'] for w in rep['work_orders']}
        # 已执行的拆检/停机记录没有被改成“从未执行”
        self.assertEqual(kinds['teardown_inspection'],'completed_confirmed')
        self.assertIn(kinds['bearing_replacement'],('open',))
        self.assertTrue(audit(self.db)['replay_ok'])

    # 6. 复采反证：撤回未执行的更换单；已执行的限速处置保留
    def test_recapture_refutes_and_withdraws_only_unexecuted_replacement(self):
        fs,n=200,200
        self.add_pump();self.add_run();self.add_sample('VS1','PR1','P1',fs,'radial',sin_wave(20,fs,n));self.add_tach('TACH1','PR1','P1')
        pump_health.disposition(self.db,self.file('d.json',
            {'id':'WO2','pump_id':'P1','run_id':'PR1','station':'hospital',
             'issued_at':'2026-01-10T10:00:00Z','signed_by':'mech_supervisor_chen'}))
        # 先证实 -> 开出更换单
        hi=1000;N=2000
        radial_hit=[round(math.sin(2*math.pi*20*k/hi)+0.5*math.sin(2*math.pi*180*k/hi),9) for k in range(N)]
        self._recapture('RC1',radial_hit,sin_wave(20,hi,N))
        rep=pump_health.health_report(self.db)
        self.assertIn('bearing_replacement',{w['kind'] for w in rep['work_orders']})
        # 后一段干净复采反证
        out=self._recapture('RC2',sin_wave(20,hi,N),sin_wave(20,hi,N),
                            start='2026-01-10T08:40:00Z',end='2026-01-10T08:40:02Z',when='2026-01-10T08:40:02Z')
        self.assertEqual(out['verdict'],'bearing_refuted')
        rep=pump_health.health_report(self.db)
        statuses={(w['kind'],w['status']) for w in rep['work_orders']}
        self.assertIn(('bearing_replacement','withdrawn_refuted'),statuses)
        # 已执行的限速轮值处置保留，备机容量承诺仍在
        self.assertIn(('limited_duty','open'),statuses)
        self.assertEqual(rep['committed_capacity_lps_by_station'],{'hospital':16})

    # 7. 时钟漂移与丢包：置信界 + 具体补采任务，判别保持 indeterminate
    def test_clock_drift_packet_loss_and_desync_bounds(self):
        fs,n=200,200
        self.add_pump();self.add_run();self.add_sample('VS1','PR1','P1',fs,'radial',sin_wave(20,fs,n));self.add_tach('TACH1','PR1','P1')
        hi=1000;N=2000
        radial=[round(math.sin(2*math.pi*20*k/hi)+0.5*math.sin(2*math.pi*180*k/hi),9) for k in range(N)]
        out=self._recapture('RC-DRIFT',radial,sin_wave(20,hi,N),clock_drift_ms=20.0)
        self.assertEqual(out['verdict'],'inadequate')
        self.assertFalse(out['confidence']['within_bounds'])
        self.assertGreater(out['confidence']['frequency_uncertainty_hz'],0.0)
        self.assertEqual(out['recapture_task'],'RT-RC-DRIFT')
        self.assertIn('clock_drift_exceeds_bound',
                      next(t for t in pump_health.health_report(self.db)['recapture_tasks']
                           if t['id']=='RT-RC-DRIFT')['reasons'])
        self.assertEqual(out['assessment_status'],'indeterminate')
        out2=self._recapture('RC-LOSS',radial,sin_wave(20,hi,N),packet_loss_pct=3.0,
                             start='2026-01-10T08:40:00Z',end='2026-01-10T08:40:02Z',when='2026-01-10T08:40:02Z')
        self.assertEqual(out2['verdict'],'inadequate')
        self.assertIn('packet_loss_exceeds_bound',
                      next(t for t in pump_health.health_report(self.db)['recapture_tasks']
                           if t['id']=='RT-RC-LOSS')['reasons'])
        out3=self._recapture('RC-DESYNC',radial,sin_wave(20,hi,N),desync_ms=12.0,
                             start='2026-01-10T08:50:00Z',end='2026-01-10T08:50:02Z',when='2026-01-10T08:50:02Z')
        self.assertEqual(out3['verdict'],'inadequate')
        self.assertIn('cross_device_desync_exceeds_bound',
                      next(t for t in pump_health.health_report(self.db)['recapture_tasks']
                           if t['id']=='RT-RC-DESYNC')['reasons'])
        self.assertTrue(audit(self.db)['replay_ok'])

    # 8. 同一机组不能在两个站点的可用容量里重复计数；多泵容量按站汇总不重号
    def test_roster_no_double_count_and_capacity_aggregation(self):
        fs,n=200,200
        self.add_pump('P1','EAST',20);self.add_run('PR1','P1')
        self.add_sample('VS1','PR1','P1',fs,'radial',sin_wave(20,fs,n));self.add_tach('TACH1','PR1','P1')
        pump_health.disposition(self.db,self.file('d1.json',
            {'id':'W1','pump_id':'P1','run_id':'PR1','station':'hospital',
             'issued_at':'2026-01-10T10:00:00Z','signed_by':'mech_supervisor_chen'}))
        with self.assertRaisesRegex(ValueError,'pump_already_committed_elsewhere'):
            pump_health.disposition(self.db,self.file('d2.json',
                {'id':'W2','pump_id':'P1','run_id':'PR1','station':'hospital',
                 'roster_station':'hospital_north',
                 'issued_at':'2026-01-10T10:05:00Z','signed_by':'mech_supervisor_chen'}))
        # 第二台泵同站限速，容量相加且各自只计一次
        self.add_pump('P2','EAST',25);self.add_run('PR2','P2',start='2026-01-10T11:00:00Z',stop='2026-01-10T12:00:00Z')
        self.add_sample('VS2','PR2','P2',fs,'axial',sin_wave(20,fs,n),when='2026-01-10T11:30:00Z')
        self.add_tach('TACH2','PR2','P2',when='2026-01-10T11:30:00Z')
        pump_health.disposition(self.db,self.file('d3.json',
            {'id':'W3','pump_id':'P2','run_id':'PR2','station':'hospital',
             'issued_at':'2026-01-10T13:00:00Z','signed_by':'mech_supervisor_chen'}))
        rep=pump_health.health_report(self.db)
        pumps={r['pump_id'] for r in rep['standby_roster']}
        self.assertEqual(pumps,{'P1','P2'})  # 每台泵仅一行
        self.assertEqual(rep['committed_capacity_lps_by_station'],{'hospital':16+20})

    # 9. 第二条运行峰值降低（负荷/滤波/安装方向同时改变）仍不能排除轴承故障
    def test_second_run_lower_peak_remains_indeterminate(self):
        fs,n=200,200
        self.add_pump()
        self.add_run('PR1');self.add_sample('VS1','PR1','P1',fs,'radial',sin_wave(20,fs,n));self.add_tach('TACH1','PR1','P1')
        self.add_run('PR2',volume=50000,energy=26000,
                 start='2026-01-11T08:00:00Z',stop='2026-01-11T09:00:00Z')
        self.add_sample('VS2','PR2','P1',fs,'axial',sin_wave(20,fs,n,amp=0.3),when='2026-01-11T08:30:00Z')
        self.add_tach('TACH2','PR2','P1',when='2026-01-11T08:30:00Z')
        a=pump_health.assess_decision(self.db,'PR2')
        self.assertEqual(a['status'],'indeterminate')
        self.assertIn('bearing_impact_aliased',{c['fault'] for c in a['candidates']})
        self.assertTrue(all(c['status']=='indistinguishable' for c in a['candidates']))

    # 10. 既有 fixture（PR1/VS1/TACH1）导入后只能“已采录”，不能宣称轴承正常
    def test_fixture_capture_cannot_claim_bearing_healthy(self):
        operations.pump(self.db,FIX/'pump-ok.json')
        operations.run(self.db,FIX/'pump-run-ok.json')
        vibration.sample(self.db,FIX/'vibration-sample-ok.json')
        vibration.tach(self.db,FIX/'tachometer-ok.json')
        a=pump_health.assess_decision(self.db,'PR1')
        self.assertNotEqual(a['status'],'bearing_refuted')
        self.assertEqual(a['status'],'indeterminate')
        self.assertTrue(a['recorder_conditions'])  # 有高率复采债务
        self.assertTrue(audit(self.db)['replay_ok'])

    # 11. 没有振动样本的运行不得产出健康结论
    def test_assessment_requires_vibration(self):
        self.add_pump();self.add_run()
        with self.assertRaisesRegex(ValueError,'no_vibration_for_run'):
            pump_health.assess_decision(self.db,'PR1')

    # 12. 处置/复采/拆检导入器的重放幂等与审计
    def test_health_imports_are_replayable(self):
        fs,n=200,200
        self.add_pump();self.add_run();self.add_sample('VS1','PR1','P1',fs,'radial',sin_wave(20,fs,n));self.add_tach('TACH1','PR1','P1')
        dfile=self.file('d.json',
            {'id':'WO1','pump_id':'P1','run_id':'PR1','station':'shutdown','issued_at':'2026-01-10T10:00:00Z'})
        self.assertFalse(pump_health.disposition(self.db,dfile)['replayed'])
        self.assertTrue(pump_health.disposition(self.db,dfile)['replayed'])
        td=self.file('td.json',
            {'id':'TD1','work_order_id':'WO1','finding':'raceway_damage','observed_at':'2026-01-11T09:00:00Z'})
        self.assertFalse(pump_health.teardown(self.db,td)['replayed'])
        self.assertTrue(pump_health.teardown(self.db,td)['replayed'])
        hi=1000;N=2000
        rc=self.file('rc.json',
            {'id':'RC1','pump_id':'P1','run_id':'PR1','sample_rate_hz':1000,'axes':['radial','axial'],
             'values':{'radial':[round(math.sin(2*math.pi*20*k/hi)+0.5*math.sin(2*math.pi*180*k/hi),9) for k in range(N)],
                       'axial':sin_wave(20,hi,N)},
             'clock_start':'2026-01-10T08:30:00Z','clock_end':'2026-01-10T08:30:02Z',
             'anti_alias_filter':{'cutoff_hz':400},'probe_position':'radial_bearing_housing',
             'load_lps':20,'tach_pulse':[i/20 for i in range(41)],'recorded_at':'2026-01-10T08:30:02Z'})
        self.assertFalse(pump_health.recapture(self.db,rc)['replayed'])
        self.assertTrue(pump_health.recapture(self.db,rc)['replayed'])
        self.assertTrue(audit(self.db)['replay_ok'])

    # 13. 复采文件硬性条件：1kHz、抗混叠滤波、同步脉冲、时间窗挂接真实运行时段
    def test_recapture_hard_conditions(self):
        fs,n=200,200
        self.add_pump();self.add_run();self.add_sample('VS1','PR1','P1',fs,'radial',sin_wave(20,fs,n));self.add_tach('TACH1','PR1','P1')
        hi=1000;N=2000
        base={'id':'RCX','pump_id':'P1','run_id':'PR1','sample_rate_hz':hi,
              'axes':['radial','axial'],
              'values':{'radial':sin_wave(20,hi,N),'axial':sin_wave(20,hi,N)},
              'clock_start':'2026-01-10T08:30:00Z','clock_end':'2026-01-10T08:30:02Z',
              'anti_alias_filter':{'cutoff_hz':400},'probe_position':'radial_bearing_housing',
              'load_lps':20,'tach_pulse':[i/20 for i in range(41)],
              'recorded_at':'2026-01-10T08:30:02Z'}
        bad=json.loads(json.dumps(base));bad['sample_rate_hz']=500
        with self.assertRaisesRegex(ValueError,'recapture_rate_below_1khz'):
            pump_health.recapture(self.db,self.file('r1.json',bad))
        bad=json.loads(json.dumps(base));bad['anti_alias_filter']=None
        with self.assertRaisesRegex(ValueError,'anti_alias_filter_required'):
            pump_health.recapture(self.db,self.file('r2.json',bad))
        bad=json.loads(json.dumps(base));bad['tach_pulse']=[]
        with self.assertRaisesRegex(ValueError,'sync_tach_pulse_required'):
            pump_health.recapture(self.db,self.file('r3.json',bad))
        bad=json.loads(json.dumps(base));bad['clock_start']='2026-01-10T07:59:59Z'
        with self.assertRaisesRegex(ValueError,'recapture_window_outside_run'):
            pump_health.recapture(self.db,self.file('r4.json',bad))
        # 原件未被污染
        self.assertEqual(report(self.db)['state']['recapture_record'],[])

    def _recapture(self,rid,radial,axial,packet_loss_pct=0.0,clock_drift_ms=0.0,desync_ms=0.0,
                   start='2026-01-10T08:30:00Z',end='2026-01-10T08:30:02Z',when='2026-01-10T08:30:02Z'):
        doc={'id':rid,'pump_id':'P1','run_id':'PR1','sample_rate_hz':1000,
             'axes':['radial','axial'],'values':{'radial':radial,'axial':axial},
             'clock_start':start,'clock_end':end,
             'anti_alias_filter':{'cutoff_hz':400,'type':'butterworth_4th'},
             'probe_position':'radial_bearing_housing','load_lps':20,
             'tach_pulse':[i/20 for i in range(41)],
             'packet_loss_pct':packet_loss_pct,'clock_drift_ms':clock_drift_ms,
             'cross_device_desync_ms':desync_ms,'recorded_at':when}
        return pump_health.recapture(self.db,self.file(rid+'.json',doc))

if __name__=='__main__':unittest.main()
