"""Industry explorer: fixed current cohorts, aligned sessions, descriptive research metrics."""
from datetime import date
from statistics import median
from math import isclose
from src.collectors import refprices
from src.database import connect
from src.quant.industry import load_industries, _level
from src.quant.screener import _load_universe_frames, screen_settings
from src.quant.themes import load_themes


def explore(as_of, path=None):
    cfg = screen_settings()
    frames, exchanges, stale = _load_universe_frames(date.fromisoformat(as_of), 300, path)
    classes = load_industries(path)
    refs = refprices.lookup([(t, f.index[-1].date().isoformat()) for t, f in frames.items() if len(f)], path)
    with connect(path) as db:
        names = dict(db.execute('SELECT ticker,name FROM tickers'))
        index = dict(db.execute("SELECT trade_date,close FROM index_daily WHERE symbol='VNINDEX' AND trade_date<=? ORDER BY trade_date", (as_of,)))
    dates = sorted(index)[-85:]
    if not dates:
        return {'groups': [], 'as_of': as_of}
    groups = {}
    for t in frames:
        lv = _level(classes[t], int(cfg['industry']['level'])) if t in classes else None
        if lv:
            groups.setdefault('industry:'+lv[0], {'name':lv[1], 'kind':'industry', 'tickers':[]})['tickers'].append(t)
    for name, ts in load_themes().items():
        groups['theme:'+name] = {'name':name,'kind':'theme','tickers':[t for t in ts if t in frames]}
    out=[]
    for key,g in groups.items():
        members=[]; valid={}
        for t in sorted(g['tickers']):
            f=frames[t]; c=f['close']; day=f.index[-1].date().isoformat()
            value=float((c*f['volume']).tail(20).mean())
            fresh=day==dates[-1] and t not in stale
            eligible=fresh and len(f)>=70 and value>=float(cfg['liquidity']['min_value_vnd'].get(exchanges[t],3e9))
            members.append({'ticker':t,'name':names.get(t,t),'exchange':exchanges[t], 'close':float(c.iloc[-1]), 'date':day,
                            'change':float((c.iloc[-1]/c.iloc[-2]-1)*100) if len(c)>1 and c.iloc[-2]>0 else None,
                            'value':value,'eligible':eligible,'flagged':t in stale})
            member = members[-1]
            ref = refs.get((t, day))
            member['change_basis'], member['limit'] = 'close', None
            if ref and refprices.plausible(member['close'], ref['reference'], exchanges[t]):
                member['change'] = refprices.board_pct(member['close'], ref, exchanges[t])
                member['change_basis'] = 'ref'
                if isclose(member['close'], ref['ceiling'], rel_tol=1e-8, abs_tol=.01):
                    member['limit'] = 'ceiling'
                elif isclose(member['close'], ref['floor'], rel_tol=1e-8, abs_tol=.01):
                    member['limit'] = 'floor'
            if eligible:
                valid[t]={d.date().isoformat():(float(r.close),float(r.close*r.volume)) for d,r in f.iterrows()}
        def snapshot(day):
            k=dates.index(day); results={}; above=[]; volumes=[]
            for t,series in valid.items():
                if day not in series:continue
                close=series[day][0]
                hist=[v[0] for d,v in sorted(series.items()) if d<=day]
                if len(hist)>=50:above.append(close>sum(hist[-50:])/50)
                for n in (5,21,63):
                    if k>=n and dates[k-n] in series and series[dates[k-n]][0]>0:
                        results.setdefault(n,[]).append((close/series[dates[k-n]][0]-1)*100)
                vals=[v[1] for d,v in sorted(series.items()) if d<=day][-25:]
                if len(vals)==25 and sum(vals[:20])>0:volumes.append((sum(vals[-5:])/5)/(sum(vals[:20])/20))
            r={str(n):round(median(v),2) for n,v in results.items() if v}
            rel=r.get('21')
            if rel is not None and k>=21 and index[dates[k-21]]>0:rel-= (index[day]/index[dates[k-21]]-1)*100
            return {'date':day,'returns':r,'relative':round(rel,2) if rel is not None else None,
                    'breadth':round(100*sum(above)/len(above),1) if above else None,
                    'liquidity':round(median(volumes),2) if volumes else None,'count':len(above)}
        history=[snapshot(d) for d in dates[-21:]]
        now=history[-1]; before=history[-6] if len(history)>=6 else {}
        delta=round(now['relative']-before['relative'],2) if now['relative'] is not None and before.get('relative') is not None else None
        breadth_delta=round(now['breadth']-before['breadth'],1) if now['breadth'] is not None and before.get('breadth') is not None else None
        status='insufficient'
        if now['count']>=3 and delta is not None and breadth_delta is not None:
            status='leading' if now['relative']>0 and now['breadth']>=50 and delta>=0 else 'improving' if delta>0 and breadth_delta>0 else 'weakening' if delta<0 and breadth_delta<0 else 'mixed'
        out.append({'id':key,**g,'tickers':None,'members':members,'metrics':now,'history':history,
                    'relative_change':delta,'breadth_change':breadth_delta,'status':status})
    return {'groups':out,'as_of':dates[-1], 'method':'Lợi nhuận trung vị; so VN-Index theo cùng phiên. Chỉ đo mã đủ thanh khoản, đủ lịch sử và có giá phiên này. Lịch sử dùng cùng tập mã hiện tại, không phải kiểm định quá khứ.'}
