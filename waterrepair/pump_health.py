"""泵机健康判别与检修工单。

低采样率波形会把高频轴承冲击折叠（混叠）成低频峰，单段 200Hz 波形无法区分
一倍转频不平衡与高频冲击的混叠像。本模块对每次 pump_run 产出：

- 可证与不可辨识的故障集合（候选源以混叠等价类形式保留，绝不按最高峰定零件）；
- 依授权产生的设备资格：可停机站点拆检停机，医院应急站点机械主管签章后限速轮值；
- 需补采的记录器条件与置信界：1kHz、抗混叠滤波、双轴、同步转速脉冲等；
- 补采回传后的阶次谱/包络谱判别、工单升级与未执行工单撤回；
- 已执行的停机/限速/拆检记录不可改写的历史，以及备机容量不重复计数。

只处理机械资产维护结论，不处理分区配水或住户账单。
"""
import json,math,hashlib
from datetime import datetime
from .store import connect,source,repeated,at

# 站级预先批准的处置
SHUTDOWN_STATION='shutdown'
HOSPITAL_STATION='hospital'
# 补采目标与判定阈值
RECATCH_RATE_HZ=1000
RECATCH_AXES=('radial','axial')
MAX_CLOCK_DRIFT_MS=5.0
MAX_DESYNC_MS=5.0
MAX_PACKET_LOSS_PCT=1.0
# 谱线判据
COHERENCE_TOL_HZ=1.0
BEARING_RATIO_MIN=0.25
# 轴承特征阶次（滚动体经过滚道，合成工况取 9 阶：20Hz 转轴 -> 180Hz 冲击）
BEARING_ORDERS=(9.0,)

# ---------- 纯标准库频谱 ----------
def dft_magnitude(values):
    """单边幅度谱（实序列），返回 (频率bin列表, 幅度列表)，频率以归一化 f/fs 计。"""
    n=len(values)
    mags=[]
    half=n//2+1
    for k in range(half):
        re=im=0.0
        for j,v in enumerate(values):
            ang=2*math.pi*k*j/n
            re+=v*math.cos(ang);im-=v*math.sin(ang)
        mags.append(math.hypot(re,im)/n)
    return [k/n for k in range(half)],mags

def spectrum(values,fs):
    bins,mags=dft_magnitude(values)
    lines=[{'hz':round(b*fs,6),'magnitude':round(m,9)} for b,m in zip(bins,mags)]
    peak=max(lines,key=lambda x:x['magnitude']) if lines else {'hz':0.0,'magnitude':0.0}
    return lines,peak

def alias_equivalence(observed_hz,fs,fmax):
    """观测频点在采样率 fs 下的混叠等价类：所有采样后落到 observed_hz 的频率。

    采样把 f 折叠到 |((f+fs/2) mod fs)-fs/2|；其逆像为 f0+k*fs ± observed。
    fmax 以内（含）的全部源频率都与观测不可区分。
    """
    out=set()
    k=0
    while True:
        added=False
        for f in (k*fs+observed_hz,k*fs-observed_hz):
            if 0<=f<=fmax:
                out.add(round(f,6));added=True
        if not added and k*fs-observed_hz>fmax:break
        k+=1
        if k>10000:break
    return sorted(out)

def envelope(values):
    """包络解调近似：|x| 移动均值后取差分，恢复被整流抹平的周期冲击能量。"""
    rect=[abs(v) for v in values]
    w=5
    env=[]
    for i in range(len(rect)):
        lo=max(0,i-w//2);hi=min(len(rect),i+w//2+1)
        env.append(sum(rect[lo:hi])/(hi-lo))
    return [env[i]-env[i-1] if i else 0.0 for i in range(len(env))]

def order_lines(lines,shaft_hz):
    out=[]
    for ln in lines:
        if ln['magnitude']<=0:continue
        out.append({'order':round(ln['hz']/shaft_hz,6) if shaft_hz else None,**ln})
    return out

def line_ratio(lines,hzt,tol):
    base=max((x['magnitude'] for x in lines),default=0.0)
    if base<=0:return 0.0
    hit=max((x['magnitude'] for x in lines if abs(x['hz']-hzt)<=tol),default=0.0)
    return hit/base

# ---------- 评估 ----------
def _load_evidence(db,run_id):
    run=db.execute('SELECT * FROM pump_run WHERE id=?',(run_id,)).fetchone()
    if not run:raise ValueError('unknown_run')
    samples=[dict(x) for x in db.execute('SELECT * FROM vibration_sample WHERE run_id=? ORDER BY id',(run_id,))]
    tachs=[dict(x) for x in db.execute('SELECT * FROM tachometer WHERE run_id=? ORDER BY id',(run_id,))]
    recaps=[dict(x) for x in db.execute('SELECT * FROM recapture_record WHERE run_id=? ORDER BY id',(run_id,))]
    teardowns=[dict(x) for x in db.execute('SELECT t.* FROM teardown_finding t JOIN work_order w ON w.id=t.work_order_id WHERE t.pump_id=? ORDER BY t.observed_at,t.id',(run['pump_id'],))]
    return run,samples,tachs,recaps,teardowns

def _build_assessment(run,samples,tachs,recaps,teardowns):
    pump_id=run['pump_id']
    if not samples:raise ValueError('no_vibration_for_run')
    shaft_hz=round(sum(t['rpm'] for t in tachs)/len(tachs)/60.0,6) if tachs else None
    # 转速稳定性
    rpm_values=[t['rpm'] for t in tachs]
    tach_stable=bool(rpm_values) and (max(rpm_values)-min(rpm_values))<=max(1,round(0.01*rpm_values[0]))
    provable=[];candidates=[];alias_class=[];rates=[]
    unresolved_debt=False;peak_hz=None
    for s in samples:
        fs=s['sample_rate_hz'];rates.append(fs)
        values=json.loads(s['values_json'])
        lines,peak=spectrum(values,fs)
        peak_hz=peak['hz'] if peak_hz is None else peak_hz
        nyq=fs/2.0
        equiv=alias_equivalence(peak['hz'],fs,RECATCH_RATE_HZ/2.0)
        alias_class.append({'sample_id':s['id'],'axis':s['axis'],'sample_rate_hz':fs,
                            'observed_peak_hz':peak['hz'],'nyquist_hz':nyq,
                            'equivalent_source_hz':equiv})
        provable.append({'sample_id':s['id'],'kind':'spectral_line',
                         'hz':peak['hz'],'magnitude':peak['magnitude'],
                         'note':'观测谱线本身可证；物理源不唯一'})
        coherent=bool(shaft_hz) and abs(peak['hz']-shaft_hz)<=COHERENCE_TOL_HZ
        if coherent:
            provable.append({'sample_id':s['id'],'kind':'tach_coherent_order1',
                             'hz':peak['hz'],'shaft_hz':shaft_hz,
                             'note':'谱峰与转速一倍转频相干；混叠像可恰好落在同频，相干性不能排除高频源'})
        else:
            provable.append({'sample_id':s['id'],'kind':'tach_incoherent',
                             'hz':peak['hz'],'shaft_hz':shaft_hz})
        if peak['hz']<nyq and len(equiv)>1:
            unresolved_debt=True
            if coherent:
                candidates.append({'sample_id':s['id'],'fault':'unbalance_1x',
                                   'source_hz':round(shaft_hz,6) if shaft_hz else None,
                                   'status':'indistinguishable',
                                   'maintenance_target':'rotor_balance',
                                   'why':'低频峰与一倍转频相干，可能为真不平衡'})
            # 只对轴承特征阶次（合成工况 9 阶：20Hz -> 180Hz）指派轴承检修对象；
            # 其余混叠镜像保留在 equivalent_source_hz，不按最高峰写确定零件
            bearing_equiv=[f for f in equiv if f>nyq and shaft_hz and
                           any(abs(f/shaft_hz-b)<=0.25 for b in BEARING_ORDERS)]
            for f in bearing_equiv:
                candidates.append({'sample_id':s['id'],'fault':'bearing_impact_aliased',
                                   'source_hz':f,'status':'indistinguishable',
                                   'maintenance_target':'bearing',
                                   'why':f'{f}Hz 轴承阶次分量在 {fs}Hz 采样下混叠为 {peak["hz"]}Hz，与不平衡不可区分'})
            if not any(c['sample_id']==s['id'] for c in candidates):
                candidates.append({'sample_id':s['id'],'fault':'spectral_line_unclassified',
                                   'source_hz':peak['hz'],'status':'indistinguishable',
                                   'maintenance_target':'none',
                                   'why':f'混叠等价类 {equiv} 中无已建档故障阶次，物理源待定'})
        elif peak['hz']>=nyq-1e-9:
            unresolved_debt=True
            candidates.append({'sample_id':s['id'],'fault':'line_at_nyquist',
                               'source_hz':peak['hz'],'status':'indistinguishable',
                               'maintenance_target':'unknown',
                               'why':'谱峰位于奈奎斯特界，折叠方向不可判'})
        else:
            candidates.append({'sample_id':s['id'],'fault':'spectral_line_unclassified',
                               'source_hz':peak['hz'],'status':'indistinguishable',
                               'maintenance_target':'none',
                               'why':'现有记录不足以指派物理故障'})
    # 补采证据
    recap_conf=[];adequate_any=False
    for r in recaps:
        conf=json.loads(r['confidence_json'])
        recap_conf.append(conf)
        adequate_any=adequate_any or r['verdict'] in ('bearing_confirmed','bearing_refuted')
    # 物理拆检与补采裁决优先
    confirmed=[t for t in teardowns if t['confirmed_fault']=='bearing_raceway_damage']
    refuted_teardown=[t for t in teardowns if t['finding']=='no_raceway_damage']
    confirmed_recap=[r for r in recaps if r['verdict']=='bearing_confirmed']
    refuted_recap=[r for r in recaps if r['verdict']=='bearing_refuted']
    if confirmed or confirmed_recap:
        status='bearing_confirmed'
        for c in candidates:
            if c['fault'].startswith('bearing'):c['status']='confirmed'
        if confirmed:
            provable.append({'kind':'teardown_confirmation',
                             'work_order_id':confirmed[-1]['work_order_id'],
                             'note':'拆检找到滚道损伤，标定轴承故障'})
    elif refuted_teardown or refuted_recap:
        status='bearing_refuted'
        for c in candidates:
            if c['fault'].startswith('bearing'):c['status']='refuted'
        provable.append({'kind':'bearing_hypothesis_refuted',
                         'note':'高率抗混叠复采/拆检未发现轴承带能量或滚道损伤；未执行的更换单撤回'})
    elif adequate_any:
        status='indeterminate'
    else:
        status='indeterminate' if unresolved_debt else 'recorded_only'
    # 记录器条件债务
    recorder_conditions=[]
    if unresolved_debt:
        recorder_conditions.append({'condition':'sample_rate','required_hz':RECATCH_RATE_HZ,
                                    'reason':'现有采样率存在混叠等价类，需覆盖轴承特征频带'})
        recorder_conditions.append({'condition':'anti_alias_filter','required':True,
                                    'reason':'采样前抑制折叠成分'})
        recorder_conditions.append({'condition':'axes','required':list(RECATCH_AXES),
                                    'reason':'单轴安装方向改变会掩盖冲击，需双轴'})
        recorder_conditions.append({'condition':'sync_tach_pulse','required':True,
                                    'reason':'同步转速脉冲用于阶次谱与跨设备同步判据'})
        recorder_conditions.append({'condition':'raw_bytes_clock','required':True,
                                    'reason':'保存原始采样字节与时钟以复核漂移与丢包'})
    confidence={'shaft_hz':shaft_hz,'tach_stable':tach_stable,
                'sample_rates_hz':sorted(set(rates)),
                'recapture_confidence':recap_conf,
                'unresolved_alias':unresolved_debt}
    return {'id':f'HA-{run["id"]}','pump_id':pump_id,'run_id':run['id'],
            'shaft_hz':shaft_hz,'sample_rates':sorted(set(rates)),'peak_hz':peak_hz,
            'status':status,'provable':provable,'candidates':candidates,
            'alias_class':alias_class,'confidence':confidence,
            'recorder_conditions':recorder_conditions}

def _persist_assessment(db,run_id,a,sid):
    db.execute('''INSERT INTO health_assessment
        (id,pump_id,run_id,shaft_hz,sample_rates_json,peak_hz,status,provable_json,
         candidates_json,alias_class_json,confidence_json,recorder_conditions_json,source_id)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET shaft_hz=excluded.shaft_hz,
         sample_rates_json=excluded.sample_rates_json,peak_hz=excluded.peak_hz,
         status=excluded.status,provable_json=excluded.provable_json,
         candidates_json=excluded.candidates_json,alias_class_json=excluded.alias_class_json,
         confidence_json=excluded.confidence_json,
         recorder_conditions_json=excluded.recorder_conditions_json,source_id=excluded.source_id''',
        (a['id'],a['pump_id'],run_id,a['shaft_hz'],json.dumps(a['sample_rates']),a['peak_hz'],
         a['status'],json.dumps(a['provable'],ensure_ascii=False),
         json.dumps(a['candidates'],ensure_ascii=False),
         json.dumps(a['alias_class'],ensure_ascii=False),
         json.dumps(a['confidence'],ensure_ascii=False),
         json.dumps(a['recorder_conditions'],ensure_ascii=False),sid))

def assess(path,run_id):
    """对一次运行重算健康判别，沿用既有决定来源；尚无来源时 source_id 留空。"""
    with connect(path) as db,db:
        run,samples,tachs,recaps,teardowns=_load_evidence(db,run_id)
        a=_build_assessment(run,samples,tachs,recaps,teardowns)
        row=db.execute('SELECT source_id FROM health_assessment WHERE id=?',(a['id'],)).fetchone()
        sid=row['source_id'] if row else None
        _persist_assessment(db,run_id,a,sid)
        return a

def assess_decision(path,run_id):
    """CLI/审计入口：追加确定性 health_decision 来源后重算（空库重放与 resident.decide 同构）。"""
    with connect(path) as db,db:
        run,samples,tachs,recaps,teardowns=_load_evidence(db,run_id)
        a=_build_assessment(run,samples,tachs,recaps,teardowns)
        raw=json.dumps({'run_id':run_id,'decision':'health_assessment'},sort_keys=True).encode()
        digest=hashlib.sha256(raw).hexdigest()
        row=db.execute('SELECT id FROM source WHERE sha=?',(digest,)).fetchone()
        sid=row['id'] if row else db.execute('INSERT INTO source(kind,sha,raw) VALUES(?,?,?)',
                                             ('health_decision',digest,raw)).lastrowid
        _persist_assessment(db,run_id,a,sid)
        return a

# ---------- 处置指令 ----------
def _open_order(db,d,sid,kind,disposition,reason,speed_limit_rpm=None,capacity_lps=None,supersedes=None):
    observed=at(d['issued_at'])
    db.execute('''INSERT INTO work_order
        (id,pump_id,run_id,kind,station,disposition,status,speed_limit_rpm,capacity_lps,
         reason_json,signed_by,supersedes,observed_at,source_id)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (d['id'],d['pump_id'],d['run_id'],kind,d['station'],disposition,'open',
         speed_limit_rpm,capacity_lps,json.dumps(reason,ensure_ascii=False),
         d.get('signed_by'),supersedes,observed,sid))
    return observed

def _commit_roster(db,pump_id,station,mode,capacity_lps,order_id,sid):
    # 同一机组不能在两个站点的可用容量里重复计数；同站更新能力走 upsert
    other=db.execute('SELECT station FROM standby_roster WHERE pump_id=? AND station<>?',
                     (pump_id,station)).fetchone()
    if other:raise ValueError('pump_already_committed_elsewhere')
    db.execute('''INSERT INTO standby_roster(pump_id,station,mode,capacity_lps,updated_by_order,source_id)
        VALUES(?,?,?,?,?,?) ON CONFLICT(pump_id) DO UPDATE SET station=excluded.station,
        mode=excluded.mode,capacity_lps=excluded.capacity_lps,
        updated_by_order=excluded.updated_by_order,source_id=excluded.source_id''',
        (pump_id,station,mode,capacity_lps,order_id,sid))

def disposition(path,file):
    """导入站级处置指令：可停机站点拆检停机；医院应急站点签章后限速轮值。"""
    with connect(path) as db,db:
        sid,d,replay=source(db,'disposition',file)
        if repeated(db,'work_order',d['id'],sid):
            return {'id':d['id'],'replayed':True}
        run=db.execute('SELECT * FROM pump_run WHERE id=? AND pump_id=?',(d['run_id'],d['pump_id'])).fetchone()
        if not run:raise ValueError('unknown_run')
        pump=db.execute('SELECT * FROM pump WHERE id=?',(d['pump_id'],)).fetchone()
        if not pump:raise ValueError('unknown_pump')
        at(d['issued_at'])
        # 任何处置前先固化当前证据下的健康判别（决定来源随指令一起归档）
        _,samples,tachs,recaps,teardowns=_load_evidence(db,d['run_id'])
        a=_build_assessment(run,samples,tachs,recaps,teardowns)
        _persist_assessment(db,d['run_id'],a,sid)
        if d['station']==SHUTDOWN_STATION:
            reason={'indistinguishable_faults':[c for c in a['candidates'] if c['status']=='indistinguishable'],
                    'rule':'不可辨识时不得宣称唯一故障；可停机站点拆检，找到滚道损伤才标定轴承故障'}
            observed=_open_order(db,d,sid,'teardown_inspection','shutdown',reason)
            db.execute('DELETE FROM standby_roster WHERE pump_id=?',(d['pump_id'],))
            db.execute('INSERT INTO standby_roster(pump_id,station,mode,capacity_lps,updated_by_order,source_id) VALUES(?,?,?,?,?,?)',
                       (d['pump_id'],d['station'],'removed',0,d['id'],sid))
            return {'id':d['id'],'replayed':False,'status':'shutdown_teardown',
                    'work_order':d['id'],'assessment_status':a['status'],
                    'standby_capacity_lps':0,'observed_at':observed}
        if d['station']==HOSPITAL_STATION:
            if not d.get('signed_by'):raise ValueError('mechanical_supervisor_signature_required')
            # roster_station 允许同一处置规程在不同站点登记备机容量，用于跨站重复计数检查
            roster_station=d.get('roster_station',d['station'])
            tach=db.execute('SELECT rpm FROM tachometer WHERE run_id=? ORDER BY id',(d['run_id'],)).fetchall()
            if not tach:raise ValueError('tachometer_required_for_speed_limit')
            rated_rpm=max(x['rpm'] for x in tach)
            speed_limit_rpm=d.get('speed_limit_rpm',round(0.8*rated_rpm))
            if speed_limit_rpm>=rated_rpm:raise ValueError('speed_limit_must_be_below_rated')
            rated_flow=pump['flow_lps']
            capacity=round(rated_flow*speed_limit_rpm/rated_rpm)
            reason={'indistinguishable_faults':[c for c in a['candidates'] if c['status']=='indistinguishable'],
                    'rule':'医院应急站点：轴承损伤未证不得停机，签章后限速轮值，能力只按限速计入'}
            observed=_open_order(db,d,sid,'limited_duty','hospital',reason,
                                 speed_limit_rpm=speed_limit_rpm,capacity_lps=capacity)
            _commit_roster(db,d['pump_id'],roster_station,'limited',capacity,d['id'],sid)
            # 限速运行同时预约带防混叠滤波的 1kHz 双轴复采与同步转速脉冲
            task_id='RT-'+d['id']
            if not db.execute('SELECT 1 FROM recapture_task WHERE id=?',(task_id,)).fetchone():
                db.execute('''INSERT INTO recapture_task
                    (id,pump_id,run_id,target_sample_rate_hz,axes_json,anti_alias_filter,
                     sync_tach_pulse,probe_position,load_condition,reasons_json,status,source_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (task_id,d['pump_id'],d['run_id'],RECATCH_RATE_HZ,json.dumps(list(RECATCH_AXES)),
                     1,1,d.get('probe_position','radial_bearing_housing'),
                     d.get('load_condition','same_as_run'),
                     json.dumps(['alias_unresolved','hospital_limited_duty'],ensure_ascii=False),
                     'open',sid))
            return {'id':d['id'],'replayed':False,'status':'limited_duty',
                    'work_order':d['id'],'assessment_status':a['status'],
                    'speed_limit_rpm':speed_limit_rpm,'standby_capacity_lps':capacity,
                    'recapture_task':task_id,'observed_at':observed}
        raise ValueError('unknown_station_disposition')

# ---------- 复采 ----------
def _open_recapture_task(db,r,sid,reasons):
    task_id='RT-'+r['id']
    db.execute('''INSERT INTO recapture_task
        (id,pump_id,run_id,target_sample_rate_hz,axes_json,anti_alias_filter,
         sync_tach_pulse,probe_position,load_condition,reasons_json,status,source_id)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
        (task_id,r['pump_id'],r['run_id'],RECATCH_RATE_HZ,json.dumps(list(RECATCH_AXES)),
         1,1,r['probe_position'],str(r['load_lps']),
         json.dumps(reasons,ensure_ascii=False),'open',sid))
    return task_id

def recapture(path,file):
    """导入补采文件：原始采样字节、时钟、抗混叠滤波器、探头位置、负荷与转速脉冲。"""
    with connect(path) as db,db:
        sid,d,replay=source(db,'recapture',file)
        if repeated(db,'recapture_record',d['id'],sid):return {'id':d['id'],'replayed':True}
        run=db.execute('SELECT * FROM pump_run WHERE id=? AND pump_id=?',(d['run_id'],d['pump_id'])).fetchone()
        if not run:raise ValueError('unknown_run')
        fs=d['sample_rate_hz']
        if fs<RECATCH_RATE_HZ:raise ValueError('recapture_rate_below_1khz')
        if not d.get('anti_alias_filter'):raise ValueError('anti_alias_filter_required')
        if not d.get('tach_pulse'):raise ValueError('sync_tach_pulse_required')
        clock_start=at(d['clock_start']);clock_end=at(d['clock_end'])
        if not (run['started_at']<=clock_start and clock_end<=run['stopped_at']):
            raise ValueError('recapture_window_outside_run')
        recorded=at(d['recorded_at'])
        axes=list(d['axes'])
        vals=d['values']
        if set(vals.keys())!=set(axes):raise ValueError('axes_values_mismatch')
        for ax in axes:
            if not vals[ax]:raise ValueError('invalid_sample')
        loss=float(d.get('packet_loss_pct',0.0))
        drift=float(d.get('clock_drift_ms',0.0))
        desync=float(d.get('cross_device_desync_ms',0.0))
        # 置信界：时钟漂移换算成最高分析频带上的频率不确定度
        span_ms=max(1.0,(datetime.fromisoformat(clock_end[:-1])-
                         datetime.fromisoformat(clock_start[:-1])).total_seconds()*1000.0)
        freq_uncertainty_hz=round(RECATCH_RATE_HZ/2.0*drift/span_ms,6)
        conf={'packet_loss_pct':loss,'clock_drift_ms':drift,
              'cross_device_desync_ms':desync,'frequency_uncertainty_hz':freq_uncertainty_hz,
              'within_bounds':loss<=MAX_PACKET_LOSS_PCT and drift<=MAX_CLOCK_DRIFT_MS and desync<=MAX_DESYNC_MS}
        # 转速脉冲 -> 转轴频率
        pulses=d['tach_pulse']
        if len(pulses)<2:raise ValueError('tach_pulse_insufficient')
        intervals=[(pulses[i+1]-pulses[i]) for i in range(len(pulses)-1)]
        mean_interval=sum(intervals)/len(intervals)
        shaft_hz=round(1.0/mean_interval,6)
        reasons=[]
        if loss>MAX_PACKET_LOSS_PCT:reasons.append('packet_loss_exceeds_bound')
        if drift>MAX_CLOCK_DRIFT_MS:reasons.append('clock_drift_exceeds_bound')
        if desync>MAX_DESYNC_MS:reasons.append('cross_device_desync_exceeds_bound')
        adequate=not reasons
        verdict='inadequate'
        order_peaks=[];envelope_peaks=[];bearing_hits=[]
        if adequate:
            tol=max(freq_uncertainty_hz,COHERENCE_TOL_HZ)
            for ax in axes:
                lines,_=spectrum(vals[ax],fs)
                ol=order_lines(lines,shaft_hz)
                order_peaks.append({'axis':ax,'orders':sorted(
                    (x for x in ol if x['magnitude']>0),key=lambda x:-x['magnitude'])[:5]})
                env_lines,_=spectrum(envelope(vals[ax]),fs)
                envelope_peaks.append({'axis':ax,'lines':sorted(
                    env_lines,key=lambda x:-x['magnitude'])[:5]})
                for bo in BEARING_ORDERS:
                    target=bo*shaft_hz
                    direct=line_ratio(lines,target,tol)
                    env_ratio=line_ratio(env_lines,target,tol)
                    if direct>=BEARING_RATIO_MIN or env_ratio>=BEARING_RATIO_MIN:
                        bearing_hits.append({'axis':ax,'bearing_order':bo,'target_hz':round(target,6),
                                             'direct_ratio':round(direct,4),
                                             'envelope_ratio':round(env_ratio,4)})
            verdict='bearing_confirmed' if bearing_hits else 'bearing_refuted'
        db.execute('''INSERT INTO recapture_record
            (id,pump_id,run_id,sample_rate_hz,axes_json,values_json,clock_start,clock_end,
             anti_alias_filter_json,probe_position,load_lps,tach_pulse_json,packet_loss_pct,
             clock_drift_ms,desync_ms,verdict,confidence_json,recorded_at,source_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (d['id'],d['pump_id'],d['run_id'],fs,json.dumps(axes),json.dumps(vals),
             clock_start,clock_end,json.dumps(d['anti_alias_filter']),d['probe_position'],
             d['load_lps'],json.dumps(pulses),loss,drift,desync,verdict,
             json.dumps(conf,ensure_ascii=False),recorded,sid))
        task_id=None
        if not adequate:
            task_id=_open_recapture_task(db,d,sid,reasons)
        else:
            old=db.execute("SELECT id FROM recapture_task WHERE pump_id=? AND run_id=? AND status='open'",
                           (d['pump_id'],d['run_id'])).fetchall()
            for t in old:db.execute("UPDATE recapture_task SET status='fulfilled' WHERE id=?",(t['id'],))
        # 重算评估并保留同一健康决定来源
        _,samples,tachs,recaps,teardowns=_load_evidence(db,d['run_id'])
        a=_build_assessment(run,samples,tachs,recaps,teardowns)
        _persist_assessment(db,d['run_id'],a,sid)
        # 工单联动
        order_effect=None
        if verdict=='bearing_confirmed':
            wo_id='WO-BRG-'+d['id']
            if not db.execute('SELECT 1 FROM work_order WHERE id=?',(wo_id,)).fetchone():
                db.execute('''INSERT INTO work_order
                    (id,pump_id,run_id,kind,station,disposition,status,speed_limit_rpm,capacity_lps,
                     reason_json,signed_by,supersedes,observed_at,source_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (wo_id,d['pump_id'],d['run_id'],'bearing_replacement',None,'confirmed',
                     'open',None,None,json.dumps({'evidence':bearing_hits},ensure_ascii=False),
                     None,None,recorded,sid))
            order_effect={'bearing_replacement_order':wo_id}
        elif verdict=='bearing_refuted':
            withdrawn=[]
            rows=db.execute("SELECT id FROM work_order WHERE pump_id=? AND kind='bearing_replacement' AND status='open'",
                            (d['pump_id'],)).fetchall()
            for w in rows:
                db.execute("UPDATE work_order SET status='withdrawn_refuted' WHERE id=?",(w['id'],));withdrawn.append(w['id'])
            order_effect={'withdrawn_replacement_orders':withdrawn}
        return {'id':d['id'],'replayed':False,'verdict':verdict,'confidence':conf,
                'bearing_hits':bearing_hits,'order_peaks':order_peaks,
                'envelope_peaks':envelope_peaks,'recapture_task':task_id,
                'assessment_status':a['status'],'order_effect':order_effect,
                'equivalent_sources_remaining':
                    [c for c in a['candidates'] if c['status']=='indistinguishable']}

# ---------- 拆检结果 ----------
def teardown(path,file):
    """登记拆检结果：只有找到滚道损伤才标定轴承故障。"""
    with connect(path) as db,db:
        sid,d,replay=source(db,'teardown',file)
        if repeated(db,'teardown_finding',d['id'],sid):return {'id':d['id'],'replayed':True}
        wo=db.execute("SELECT * FROM work_order WHERE id=? AND kind='teardown_inspection'",
                      (d['work_order_id'],)).fetchone()
        if not wo:raise ValueError('unknown_teardown_order')
        observed=at(d['observed_at'])
        if observed<wo['observed_at']:raise ValueError('finding_before_work_order')
        finding=d['finding']
        if finding not in ('raceway_damage','no_raceway_damage'):raise ValueError('invalid_finding')
        confirmed='bearing_raceway_damage' if finding=='raceway_damage' else None
        db.execute('''INSERT INTO teardown_finding
            (id,work_order_id,pump_id,finding,confirmed_fault,observed_at,source_id)
            VALUES(?,?,?,?,?,?,?)''',
            (d['id'],wo['id'],wo['pump_id'],finding,confirmed,observed,sid))
        db.execute("UPDATE work_order SET status=? WHERE id=?",
                   ('completed_confirmed' if confirmed else 'completed_clear',wo['id']))
        if confirmed:
            repl='WO-BRG-'+d['id']
            if not db.execute('SELECT 1 FROM work_order WHERE id=?',(repl,)).fetchone():
                db.execute('''INSERT INTO work_order
                    (id,pump_id,run_id,kind,station,disposition,status,speed_limit_rpm,capacity_lps,
                     reason_json,signed_by,supersedes,observed_at,source_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (repl,wo['pump_id'],wo['run_id'],'bearing_replacement',wo['station'],
                     'confirmed','open',None,None,
                     json.dumps({'teardown':d['id']},ensure_ascii=False),None,None,observed,sid))
        # 已执行的停机/限速/拆检记录保留；只撤回尚未执行的更换单
        else:
            rows=db.execute("SELECT id FROM work_order WHERE pump_id=? AND kind='bearing_replacement' AND status='open'",
                            (wo['pump_id'],)).fetchall()
            for w in rows:
                db.execute("UPDATE work_order SET status='withdrawn_refuted' WHERE id=?",(w['id'],))
        run_id=wo['run_id']
        _,samples,tachs,recaps,teardowns=_load_evidence(db,run_id)
        run=db.execute('SELECT * FROM pump_run WHERE id=?',(run_id,)).fetchone()
        a=_build_assessment(run,samples,tachs,recaps,teardowns)
        _persist_assessment(db,run_id,a,sid)
        return {'id':d['id'],'replayed':False,'finding':finding,
                'confirmed_fault':confirmed,'assessment_status':a['status']}

# ---------- 报告 ----------
def health_report(path,run_id=None):
    with connect(path) as db:
        def rows(sql,args=()):return [dict(x) for x in db.execute(sql,args)]
        assessments=rows('SELECT * FROM health_assessment ORDER BY id'
                         +(' WHERE run_id=?' if run_id else ''),
                         (run_id,) if run_id else ())
        for a in assessments:
            for k in ('sample_rates_json','provable_json','candidates_json','alias_class_json',
                      'confidence_json','recorder_conditions_json'):
                a[k.removesuffix('_json') if k.endswith('_json') else k]=json.loads(a.pop(k))
        tasks=rows('SELECT * FROM recapture_task ORDER BY id')
        for t in tasks:t['axes']=json.loads(t.pop('axes_json'));t['reasons']=json.loads(t.pop('reasons_json'))
        recaps=rows('SELECT * FROM recapture_record ORDER BY id')
        for r in recaps:
            for k in ('axes_json','values_json','anti_alias_filter_json','tach_pulse_json','confidence_json'):
                r[k.removesuffix('_json')]=json.loads(r.pop(k))
        orders=rows('SELECT * FROM work_order ORDER BY observed_at,id')
        for o in orders:o['reason']=json.loads(o.pop('reason_json'))
        findings=rows('SELECT * FROM teardown_finding ORDER BY observed_at,id')
        roster=rows('SELECT * FROM standby_roster ORDER BY pump_id')
        # 备机容量按站汇总；同一 pump_id 只在一行，天然不重复计数
        capacity={}
        for r in roster:
            if r['mode']=='removed':continue
            capacity.setdefault(r['station'],0)
            capacity[r['station']]+=r['capacity_lps'] or 0
        return {'assessments':assessments,'recapture_tasks':tasks,
                'recapture_records':recaps,'work_orders':orders,
                'teardown_findings':findings,'standby_roster':roster,
                'committed_capacity_lps_by_station':capacity}
