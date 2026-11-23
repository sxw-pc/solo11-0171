"""施工路权覆盖求解与开挖许可卷宗。

维修隔离(repair 处于 isolated)之后, 对开挖施工单独裁决, 主营结果是
可以合法开工的路线、占用地块、许可机关及开工窗口, 而非复供状态。

原则:
- 以整条开挖带构造施工占用面(路径缓冲矩形 + 关节外接八边形, 保守外包),
  绝不用线路中心点做多边形命中; 地表占用按方法区分: open-cut 占整条开挖带,
  directional-drill 只占出入土基坑; 设备回转与开挖带一并构成安全包络,
  用于燃气安全带与路障判定(安全包络不计入地表占地)。
- 地籍边界带测绘误差: 对地块做 ±precision 的侵蚀/膨胀, 占用判定分
  definite(必占)/uncertain(待复测)/none(必不占)三值; 不确定且无权源
  一律待复测(resurvey), 不得挑有利地图猜许可。
- 候选路线 = corridor x method_spec, 同受起终点、施工方法、最小转弯半径、
  挖机宽度、封路窗口与多方同意版本约束; 批量作业共享同一车道封闭名额时
  按工点计数, 不能逐张独立发许可。
- 权源按 地块+工点+方法+窗口 匹配: 道路临时封闭许可(closure)、私人
  限界地役权(easement, 含赔付条款)、土地同意书(land_consent)。
  open-cut 同意书不能给定向顶管用, 某工点的权利凭证不能附到另一工点。
- 撤销旧测绘、迟到燃气图、权人撤回同意后: 已开工事实保留, 未开工许可
  撤回或重审并给出替代路线。
- 输出可核对的几何交集、权利链、机关、时间与费用责任; 无可行路线时
  明确列出冲突证据。原件 SHA、同事件幂等、异内容拒绝、audit 重放一致。
"""
import hashlib,json,math
from datetime import date,timedelta
from .store import connect,source,repeated,at

DEFAULT_CRS='LOCAL-GRID'
DETACH_TOL_M=5.0      # 通道起点与工点的最大允许偏差
AREA_TOL=1e-9         # 交集面积阈值(平方米), 纯接触不算占用
_SEC=1/math.cos(math.pi/8)  # 外接八边形半径系数(保守外包真圆)

# ---------------- 平面几何 ----------------
def _sub(a,b):return (a[0]-b[0],a[1]-b[1])
def _cross(a,b):return a[0]*b[1]-a[1]*b[0]

def _area(poly):
    return abs(sum(_cross(poly[i],poly[(i+1)%len(poly)]) for i in range(len(poly)))/2) if len(poly)>=3 else 0.0

def _ccw(poly):
    s=sum(_cross(poly[i],poly[(i+1)%len(poly)]) for i in range(len(poly)))
    return list(poly) if s>0 else list(reversed(poly))

def is_convex(poly):
    sign=0
    n=len(poly)
    for i in range(n):
        c=_cross(_sub(poly[(i+1)%n],poly[i]),_sub(poly[(i+2)%n],poly[(i+1)%n]))
        if abs(c)<1e-12:continue
        s=1 if c>0 else -1
        if sign and s!=sign:return False
        sign=s
    return True

def _isect(p,q,pt,d):
    den=_cross(d,_sub(p,q))
    if abs(den)<1e-18:return q
    t=_cross(d,_sub(p,pt))/den
    return (p[0]+t*(q[0]-p[0]),p[1]+t*(q[1]-p[1]))

def _clip_half(subject,pt,d):
    # 保留有向线 pt+t*d 左侧半平面
    out=[]
    n=len(subject)
    for i in range(n):
        p,q=subject[i-1],subject[i]
        pin=_cross(d,_sub(p,pt))>=-1e-9;qin=_cross(d,_sub(q,pt))>=-1e-9
        if qin:
            if not pin:out.append(_isect(p,q,pt,d))
            out.append(q)
        elif pin:out.append(_isect(p,q,pt,d))
    return out

def _clip(subject,clip):
    # Sutherland-Hodgman: 任意多边形被凸多边形(逆时针)裁剪
    out=list(subject)
    n=len(clip)
    for i in range(n):
        if not out:break
        out=_clip_half(out,clip[i],_sub(clip[(i+1)%n],clip[i]))
    return out

def _offset(poly,d):
    # 凸多边形(逆时针)等距偏移: d>0 外扩(尖角外包真圆角), d<0 内缩(凸域精确)
    out=[(-1e7,-1e7),(1e7,-1e7),(1e7,1e7),(-1e7,1e7)]
    n=len(poly)
    for i in range(n):
        a,b=poly[i],poly[(i+1)%n]
        dx,dy=b[0]-a[0],b[1]-a[1]
        L=math.hypot(dx,dy)
        if L<1e-12:continue
        nx,ny=-dy/L,dx/L  # 内侧(左)法向
        out=_clip_half(out,(a[0]-nx*d,a[1]-ny*d),(dx,dy))
        if not out:return []
    return out

def _octagon(c,r):
    R=r*_SEC
    return [(c[0]+R*math.cos(k*math.pi/4),c[1]+R*math.sin(k*math.pi/4)) for k in range(8)]

def _band_pieces(path,half):
    # 开挖带分解为凸构件: 每段矩形 + 每顶点关节八边形(外接, 保守)
    pieces=[]
    for i in range(len(path)-1):
        a,b=path[i],path[i+1]
        dx,dy=b[0]-a[0],b[1]-a[1]
        L=math.hypot(dx,dy)
        if L<1e-12:continue
        nx,ny=-dy/L,dx/L
        pieces.append((f'segment-{i}',[(a[0]+nx*half,a[1]+ny*half),(b[0]+nx*half,b[1]+ny*half),
                                      (b[0]-nx*half,b[1]-ny*half),(a[0]-nx*half,a[1]-ny*half)]))
    for i,p in enumerate(path):pieces.append((f'joint-{i}',_octagon(p,half)))
    return pieces

def _turn_radius(a,b,c):
    # 顶点 b 处内切圆半径: s/tan(theta/2), s 为较短邻边的一半
    v1=_sub(a,b);v2=_sub(c,b)
    l1=math.hypot(*v1);l2=math.hypot(*v2)
    if l1<1e-12 or l2<1e-12:return None
    cos=min(1.0,max(-1.0,(-v1[0]*v2[0]-v1[1]*v2[1])/(l1*l2)))
    theta=math.acos(cos)
    if theta<1e-9:return None
    return (min(l1,l2)/2)/math.tan(theta/2)

# ---------------- 图件有效性 ----------------
def _rows(db,sql,args=()):return [dict(x) for x in db.execute(sql,args)]

def _revoked(db):return {r['survey_version'] for r in db.execute('SELECT survey_version FROM survey_revocation')}

def _effective_parcels(db,moment):
    revoked=_revoked(db);best={}
    for p in _rows(db,'SELECT * FROM parcel'):
        if p['survey_version'] in revoked:continue
        if p['valid_from'] and p['valid_from']>moment:continue
        key=(p['valid_from'] or '',p['survey_version'])
        if p['id'] not in best or key>best[p['id']][0]:best[p['id']]=(key,p)
    return [v[1] for _,v in sorted(best.items())]

def _effective_maps(db,table,moment):
    revoked=_revoked(db)
    return [r for r in _rows(db,f'SELECT * FROM {table} ORDER BY id')
            if r['survey_version'] not in revoked and r['valid_from']<=moment]

# ---------------- 覆盖判定 ----------------
def _poly_overlap(pieces,record):
    # 三值判定: definite=必占(与内缩域相交), uncertain=待复测(仅与外扩域相交), none=必不占
    poly=_ccw([tuple(map(float,p)) for p in json.loads(record['polygon_json'])])
    eps=(record['precision_cm'] or 0)/100.0
    eroded=_offset(poly,-eps) if eps else poly
    dilated=_offset(poly,eps) if eps else poly
    definite=False;possible=False;inters=[]
    for label,piece in pieces:
        if _area(_clip(piece,dilated))<=AREA_TOL:continue
        possible=True
        inter=_clip(piece,poly)
        a=_area(inter)
        if a>AREA_TOL:
            inters.append({'piece':label,'polygon':[[round(x,6),round(y,6)] for x,y in inter],'area_m2':round(a,6)})
        if eroded and _area(_clip(piece,eroded))>AREA_TOL:definite=True
    if definite:return 'definite',inters
    if possible:return 'uncertain',inters
    return 'none',[]

def _belt_overlap(pieces,gas):
    path=[tuple(map(float,p)) for p in json.loads(gas['path_json'])]
    belt=_band_pieces(path,gas['belt_cm']/200.0)
    eps=(gas['precision_cm'] or 0)/100.0
    definite=possible=None
    for label,piece in pieces:
        for blabel,b in belt:
            outer=_offset(b,eps) if eps else b
            if _area(_clip(piece,outer))>AREA_TOL:
                possible=possible or {'piece':label,'belt_piece':blabel}
            inner=_offset(b,-eps) if eps else b
            if inner and _area(_clip(piece,inner))>AREA_TOL:
                definite=definite or {'piece':label,'belt_piece':blabel}
    if definite:return 'definite',definite
    if possible:return 'uncertain',possible
    return 'none',None

# ---------------- 权源与窗口 ----------------
def _find_rights(db,parcel,worksite_id,method):
    out=[]
    for c in _rows(db,'SELECT * FROM closure WHERE parcel_id=? ORDER BY id',(parcel['id'],)):
        if c['method'] and c['method']!=method:continue
        out.append({'kind':'closure','id':c['id'],'parcel_id':c['parcel_id'],'grantor':c['authority'],
                    'cost':c['cost'],'lane':c['lane'],'method':c['method'],'quota':c['quota'],
                    'valid_from':c['window_from'],'valid_to':c['window_to'],'window':[c['window_from'],c['window_to']]})
    for e in _rows(db,"SELECT * FROM easement WHERE parcel_id=? AND worksite_id=? AND method=? AND status='active' ORDER BY id",
                   (parcel['id'],worksite_id,method)):
        out.append({'kind':'easement','id':e['id'],'parcel_id':e['parcel_id'],'grantor':'OWNER:'+parcel['owner'],
                    'cost':'PER-AGREEMENT','method':e['method'],'valid_from':e['valid_from'],'valid_to':e['valid_to'],
                    'window':[e['valid_from'],e['valid_to']],'daily_from':e['daily_from'],'daily_to':e['daily_to'],
                    'daily':[e['daily_from'],e['daily_to']] if e['daily_from'] else None,
                    'compensation':json.loads(e['compensation_json'] or '{}')})
    for l in _rows(db,'SELECT * FROM land_consent WHERE parcel_id=? AND worksite_id=? AND method=? ORDER BY id',
                   (parcel['id'],worksite_id,method)):
        out.append({'kind':'land_consent','id':l['id'],'parcel_id':l['parcel_id'],'grantor':'OWNER:'+parcel['owner'],
                    'cost':'PER-AGREEMENT','method':l['method'],'valid_from':l['valid_from'],'valid_to':l['valid_to'],
                    'window':[l['valid_from'],l['valid_to']]})
    out.sort(key=lambda r:(r['kind'],r['id']))
    return out

def _add_days(day,k):return (date.fromisoformat(day)+timedelta(days=k)).isoformat()

def _earliest_start(moment,rights):
    # 满足全部权源有效期与每日窗口的最早开工时刻; 62 天内无解返回 None
    lo=moment
    for r in rights:lo=max(lo,r['valid_from'])
    hi=min(r['valid_to'] for r in rights)
    dailies=[(r['daily_from'],r['daily_to']) for r in rights if r.get('daily_from')]
    df=max([d[0] for d in dailies],default=None)
    dt=min([d[1] for d in dailies],default=None)
    if df and dt and df>dt:return None
    for k in range(62):
        day=_add_days(lo[:10],k)
        cand=max(lo,f'{day}T00:00:00Z')
        if df:
            cand=max(cand,f'{day}T{df}:00Z')
            if cand>f'{day}T{dt}:00Z':continue
        if cand>hi:return None
        return cand
    return None

def _closure_users(db,closure_id,exclude_worksite):
    # 共享同一车道封闭名额的工点(最新决定仍获批者, 含已开工)
    latest={}
    for row in db.execute('SELECT worksite_id,seq,status,detail_json FROM route_permit ORDER BY worksite_id,seq'):
        latest[row['worksite_id']]=row
    users=set()
    for wid,row in latest.items():
        if wid==exclude_worksite or row['status']!='approved':continue
        for r in json.loads(row['detail_json'])['routes']:
            if r['status']=='approved' and any(x['kind']=='closure' and x['id']==closure_id for x in r['rights_chain']):
                users.add(wid);break
    return users

# ---------------- 路线求解 ----------------
def _eval_route(db,worksite,corridor,spec,parcels,gases,obstacles,moment):
    path=[(float(x),float(y)) for x,y in json.loads(corridor['path_json'])]
    half=corridor['width_cm']/200.0
    wx,wy=float(worksite['x']),float(worksite['y'])
    method=spec['id']
    conflicts=[];resurvey=[]
    d0=math.hypot(path[0][0]-wx,path[0][1]-wy)
    if d0>DETACH_TOL_M:
        conflicts.append({'type':'corridor_detached','distance_m':round(d0,6),'tolerance_m':DETACH_TOL_M})
    if corridor['width_cm']<spec['machine_width_cm']:
        conflicts.append({'type':'corridor_too_narrow','width_cm':corridor['width_cm'],'required_cm':spec['machine_width_cm']})
    for i in range(1,len(path)-1):
        r=_turn_radius(path[i-1],path[i],path[i+1])
        if r is not None and r<spec['min_turn_radius_m']:
            conflicts.append({'type':'turn_too_sharp','vertex':list(path[i]),'radius_m':round(r,6),'required_m':spec['min_turn_radius_m']})
    band=_band_pieces(path,half)
    if method=='directional-drill':
        surface=[('pit-start',_octagon(path[0],half)),('pit-end',_octagon(path[-1],half))]
    else:
        surface=band
    safety=list(band)
    if spec['swing_cm']:safety.append(('swing',_octagon((wx,wy),spec['swing_cm']/100.0)))
    occupies=[];rights=[]
    for p in parcels:
        st,inters=_poly_overlap(surface,p)
        if st=='none':continue
        occupies.append({'parcel_id':p['id'],'owner':p['owner'],'survey_version':p['survey_version'],
                         'overlap':st,'intersections':inters})
        cands=_find_rights(db,p,worksite['id'],method)
        if st=='definite' and not cands:
            conflicts.append({'type':'missing_right','parcel_id':p['id'],'owner':p['owner'],'method':method})
        elif st=='uncertain' and not cands:
            resurvey.append({'type':'uncertain_parcel','parcel_id':p['id'],'owner':p['owner']})
        if cands:rights.append(cands[0])
    gas_hits=[]
    for g in gases:
        st,evidence=_belt_overlap(safety,g)
        if st=='definite':conflicts.append({'type':'gas_belt_crossing','gas_line_id':g['id'],'evidence':evidence})
        elif st=='uncertain':resurvey.append({'type':'uncertain_gas_belt','gas_line_id':g['id'],'evidence':evidence})
        if st!='none':gas_hits.append({'gas_line_id':g['id'],'overlap':st})
    obstacle_hits=[]
    for o in obstacles:
        st,inters=_poly_overlap(safety,o)
        if st=='definite':conflicts.append({'type':'obstacle_intrusion','obstacle_id':o['id'],'evidence':inters[:1]})
        elif st=='uncertain':resurvey.append({'type':'uncertain_obstacle','obstacle_id':o['id']})
        if st!='none':obstacle_hits.append({'obstacle_id':o['id'],'overlap':st})
    if not parcels:resurvey.append({'type':'cadastre_unavailable'})
    earliest=None
    if rights:
        earliest=_earliest_start(moment,rights)
        if earliest is None:conflicts.append({'type':'no_valid_window'})
    for r in rights:
        if r['kind']!='closure':continue
        users=_closure_users(db,r['id'],worksite['id'])
        if worksite['id'] not in users and len(users)>=r['quota']:
            conflicts.append({'type':'closure_quota_exhausted','closure_id':r['id'],'quota':r['quota'],'users':sorted(users)})
    status='rejected' if conflicts else ('resurvey' if resurvey else 'approved')
    if status=='approved' and not rights:earliest=moment
    if status!='approved':earliest=None
    costs={r['cost'] for r in rights}
    cost=None if not rights else (costs.pop() if len(costs)==1 else 'MIXED')
    return {'route_id':f"{corridor['id']}/{method}",'corridor_id':corridor['id'],'method':method,'status':status,
            'earliest_start':earliest,'occupies':occupies,'gas':gas_hits,'obstacles':obstacle_hits,
            'rights_chain':rights,'authorities':sorted({r['grantor'] for r in rights}),
            'cost_responsibility':cost,'conflicts':conflicts,'resurvey':resurvey}

def _solve_worksite(db,worksite,moment):
    corridors=_rows(db,'SELECT * FROM corridor WHERE worksite_id=? ORDER BY id',(worksite['id'],))
    specs=_rows(db,'SELECT * FROM method_spec ORDER BY id')
    if not specs:raise ValueError('method_spec_missing')
    parcels=_effective_parcels(db,moment)
    gases=_effective_maps(db,'gas_line',moment)
    obstacles=_effective_maps(db,'obstacle',moment)
    crs={p['crs'] for p in parcels}|{g['crs'] for g in gases}|{o['crs'] for o in obstacles}
    if len(crs)>1:raise ValueError('crs_conflict')
    if not corridors:
        return 'rejected',{'worksite_id':worksite['id'],'routes':[],'conflicts':[{'type':'no_corridor'}]}
    routes=[_eval_route(db,worksite,c,s,parcels,gases,obstacles,moment) for c in corridors for s in specs]
    statuses={r['status'] for r in routes}
    status='approved' if 'approved' in statuses else ('resurvey' if 'resurvey' in statuses else 'rejected')
    return status,{'worksite_id':worksite['id'],'routes':routes}

def _latest(db,worksite_id):
    return db.execute('SELECT * FROM route_permit WHERE worksite_id=? ORDER BY seq DESC LIMIT 1',(worksite_id,)).fetchone()

def _next_seq(db,worksite_id):
    row=_latest(db,worksite_id)
    return (row['seq'] if row else 0)+1

def _latest_view(db,repair_id):
    out=[]
    for ws in _rows(db,'SELECT * FROM worksite WHERE repair_id=? ORDER BY id',(repair_id,)):
        row=_latest(db,ws['id'])
        if row:out.append({'worksite_id':ws['id'],'seq':row['seq'],'status':row['status'],
                           'routes':json.loads(row['detail_json'])['routes']})
    return out

def decide(path,repair_id,at_time):
    # 隔离之后对开挖施工单独裁决; 同 (repair, at) 幂等, 全量决定落 route_permit 表
    moment=at(at_time)
    with connect(path) as db,db:
        rep=db.execute('SELECT * FROM repair WHERE id=?',(repair_id,)).fetchone()
        if not rep:raise ValueError('unknown_repair')
        if rep['status']!='isolated':raise ValueError('repair_not_isolated')
        raw=json.dumps({'repair_id':repair_id,'at':moment},sort_keys=True).encode()
        sha=hashlib.sha256(raw).hexdigest()
        if db.execute('SELECT 1 FROM source WHERE sha=?',(sha,)).fetchone():
            return {'repair_id':repair_id,'decided_at':moment,'replayed':True,'worksites':_latest_view(db,repair_id)}
        db.execute("INSERT INTO source(kind,sha,raw) VALUES('route_decide',?,?)",(sha,raw))
        worksites=_rows(db,'SELECT * FROM worksite WHERE repair_id=? ORDER BY id',(repair_id,))
        if not worksites:raise ValueError('worksite_missing')
        out=[]
        for ws in worksites:
            status,detail=_solve_worksite(db,ws,moment)
            seq=_next_seq(db,ws['id'])
            detail['alternatives']=[r['route_id'] for r in detail['routes'] if r['status']=='approved']
            db.execute('INSERT INTO route_permit(worksite_id,seq,status,decided_at,detail_json) VALUES(?,?,?,?,?)',
                       (ws['id'],seq,status,moment,json.dumps(detail,sort_keys=True)))
            out.append({'worksite_id':ws['id'],'seq':seq,'status':status,'routes':detail['routes']})
        statuses={o['status'] for o in out}
        overall='approved' if statuses=={'approved'} else ('resurvey' if 'resurvey' in statuses else 'rejected')
        return {'repair_id':repair_id,'decided_at':moment,'status':overall,'worksites':out,'replayed':False}

def readjudicate(db):
    # 图件/权源事件触发: 未开工工点按原裁决时刻重审; 已开工事实保留。
    # 获批被撤且无路可批记 withdrawn; 重审后可行路线写入 alternatives。
    started={r['worksite_id'] for r in db.execute('SELECT worksite_id FROM route_start')}
    latest={}
    for row in db.execute('SELECT * FROM route_permit ORDER BY worksite_id,seq'):
        latest[row['worksite_id']]=row
    for wid in sorted(latest):
        if wid in started:continue
        row=latest[wid]
        ws=db.execute('SELECT * FROM worksite WHERE id=?',(wid,)).fetchone()
        if not ws:continue
        try:status,detail=_solve_worksite(db,dict(ws),row['decided_at'])
        except ValueError as e:
            if str(e)=='crs_conflict':continue
            raise
        prev=json.loads(row['detail_json'])
        if json.dumps(prev.get('routes',[]),sort_keys=True)==json.dumps(detail['routes'],sort_keys=True):continue
        if row['status']=='approved' and status=='rejected':status='withdrawn'
        detail['supersedes']=row['id']
        detail['alternatives']=[r['route_id'] for r in detail['routes'] if r['status']=='approved']
        db.execute('INSERT INTO route_permit(worksite_id,seq,status,decided_at,detail_json) VALUES(?,?,?,?,?)',
                   (wid,row['seq']+1,status,row['decided_at'],json.dumps(detail,sort_keys=True)))

# ---------------- 采录入口 ----------------
def gas_line(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'gas_line',file)
        if repeated(db,'gas_line',d['id'],sid):return {'id':d['id'],'replayed':True}
        if len(d['path'])<2 or int(d['belt_cm'])<=0:raise ValueError('invalid_gas_line')
        db.execute('INSERT INTO gas_line VALUES(?,?,?,?,?,?,?,?)',
                   (d['id'],json.dumps(d['path'],sort_keys=True),int(d['belt_cm']),d.get('crs',DEFAULT_CRS),
                    int(d.get('precision_cm',0)),d['survey_version'],at(d['valid_from']),sid))
        readjudicate(db)
        return {'id':d['id'],'replayed':False}

def obstacle(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'obstacle',file)
        if repeated(db,'obstacle',d['id'],sid):return {'id':d['id'],'replayed':True}
        poly=[tuple(map(float,p)) for p in d['polygon']]
        if len(poly)<3 or not is_convex(poly):raise ValueError('obstacle_not_convex')
        db.execute('INSERT INTO obstacle VALUES(?,?,?,?,?,?,?)',
                   (d['id'],json.dumps(d['polygon'],sort_keys=True),d.get('crs',DEFAULT_CRS),
                    int(d.get('precision_cm',0)),d['survey_version'],at(d['valid_from']),sid))
        readjudicate(db)
        return {'id':d['id'],'replayed':False}

def closure(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'closure',file)
        if repeated(db,'closure',d['id'],sid):return {'id':d['id'],'replayed':True}
        wf,wt=at(d['window_from']),at(d['window_to'])
        if wf>=wt or int(d['quota'])<1 or not d['authority'] or not d['cost']:raise ValueError('invalid_closure')
        db.execute('INSERT INTO closure VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (d['id'],d['parcel_id'],d.get('lane',''),d.get('method'),int(d['quota']),wf,wt,d['authority'],d['cost'],sid))
        readjudicate(db)
        return {'id':d['id'],'replayed':False}

def _hhmm(v):
    return isinstance(v,str) and len(v)==5 and v[2]==':' and v[:2].isdigit() and v[3:].isdigit() and int(v[:2])<=23 and int(v[3:])<=59

def easement(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'easement',file)
        if repeated(db,'easement',d['id'],sid):return {'id':d['id'],'replayed':True}
        vf,vt=at(d['valid_from']),at(d['valid_to'])
        if vf>=vt:raise ValueError('easement_window_empty')
        df,dt=d.get('daily_from'),d.get('daily_to')
        if (df is None)!=(dt is None) or (df is not None and not(_hhmm(df) and _hhmm(dt))):
            raise ValueError('invalid_daily_window')
        if not d['method']:raise ValueError('invalid_easement')
        db.execute('INSERT INTO easement VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (d['id'],d['parcel_id'],d['worksite_id'],d['method'],vf,vt,df,dt,
                    json.dumps(d.get('compensation',{}),sort_keys=True),'active',sid))
        readjudicate(db)
        return {'id':d['id'],'replayed':False}

def method_spec(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'method_spec',file)
        if repeated(db,'method_spec',d['id'],sid):return {'id':d['id'],'replayed':True}
        if int(d['machine_width_cm'])<=0 or float(d['min_turn_radius_m'])<0 or int(d['swing_cm'])<0:
            raise ValueError('invalid_method_spec')
        db.execute('INSERT INTO method_spec VALUES(?,?,?,?,?)',
                   (d['id'],int(d['machine_width_cm']),float(d['min_turn_radius_m']),int(d['swing_cm']),sid))
        readjudicate(db)
        return {'id':d['id'],'replayed':False}

def survey_revoke(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'survey_revoke',file)
        if repeated(db,'survey_revocation',d['id'],sid):return {'id':d['id'],'replayed':True}
        db.execute('INSERT INTO survey_revocation VALUES(?,?,?,?)',(d['id'],d['survey_version'],at(d['revoked_at']),sid))
        readjudicate(db)
        return {'id':d['id'],'replayed':False}

def easement_withdraw(path,file):
    with connect(path) as db,db:
        sid,d,replay=source(db,'easement_withdraw',file)
        if repeated(db,'easement_withdrawal',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM easement WHERE id=?',(d['easement_id'],)).fetchone():
            raise ValueError('unknown_easement')
        db.execute("UPDATE easement SET status='withdrawn' WHERE id=?",(d['easement_id'],))
        db.execute('INSERT INTO easement_withdrawal VALUES(?,?,?,?)',(d['id'],d['easement_id'],at(d['withdrawn_at']),sid))
        readjudicate(db)
        return {'id':d['id'],'easement_id':d['easement_id'],'status':'withdrawn'}

def start(path,file):
    # 开工登记: 已公告/已开工事实, 之后的重审不再触动该工点
    with connect(path) as db,db:
        sid,d,replay=source(db,'route_start',file)
        if repeated(db,'route_start',d['id'],sid):return {'id':d['id'],'replayed':True}
        moment=at(d['at'])
        latest=_latest(db,d['worksite_id'])
        route=None
        if latest and latest['status']=='approved':
            route=next((r for r in json.loads(latest['detail_json'])['routes']
                        if r['corridor_id']==d['corridor_id'] and r['method']==d['method'] and r['status']=='approved'),None)
        if not route:raise ValueError('route_not_approved')
        if route['earliest_start'] and moment<route['earliest_start']:raise ValueError('start_before_window')
        db.execute('INSERT INTO route_start VALUES(?,?,?,?,?,?)',(d['id'],d['worksite_id'],d['corridor_id'],d['method'],moment,sid))
        return {'id':d['id'],'status':'started'}

def permit_report(path):
    with connect(path) as db:
        latest={}
        for row in db.execute('SELECT * FROM route_permit ORDER BY worksite_id,seq'):
            latest[row['worksite_id']]=row
        return {'permits':[{'worksite_id':w,'seq':r['seq'],'status':r['status'],'decided_at':r['decided_at'],
                            'detail':json.loads(r['detail_json'])} for w,r in sorted(latest.items())],
                'starts':_rows(db,'SELECT * FROM route_start ORDER BY id')}
