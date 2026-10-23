"""Industry explorer: fixed current cohorts, aligned sessions, descriptive research metrics."""
from datetime import date
from statistics import median
from math import isclose, isfinite
from src.collectors import refprices
from src.database import connect
from src.quant.industry import load_industries, _level
from src.quant.screener import _load_universe_frames, screen_settings
from src.quant.themes import load_themes


def rank_and_classify(groups):
    """Rank within comparable cohorts; themes never displace formal industries."""
    for g in groups:
        for h in g['history']:
            h['ranks'], h['peer_counts'] = {}, {}
    for kind in {g['kind'] for g in groups}:
        peers = [g for g in groups if g['kind'] == kind]
        for pos in range(21):
            for window in ('20', '60'):
                eligible = [g['history'][pos] for g in peers
                            if len(g['history']) > pos and g['history'][pos]['counts'].get(window, 0) >= 3
                            and g['history'][pos]['excess'].get(window) is not None]
                for h in eligible:
                    v = h['excess'][window]
                    h.setdefault('ranks', {})[window] = round(100 * sum(x['excess'][window] <= v for x in eligible) / len(eligible), 1)
                    h.setdefault('peer_counts', {})[window] = len(eligible)
    for g in groups:
        m = g['metrics']; r = m['returns']; ex = m['excess']; ranks = m.get('ranks', {})
        enough = min(m['counts'].get('20', 0), m['counts'].get('60', 0)) >= 3
        persistence = sum(h.get('ranks', {}).get('20', 0) >= 75 and h.get('excess', {}).get('20', -1) > 0 for h in g['history'][-5:])
        checks = [
            ('strength', 'Sức mạnh 20/60 phiên', enough and r.get('20', -1) > 0 and ex.get('20', -1) > 0 and ex.get('60', -1) > 0 and min(ranks.get('20', 0), ranks.get('60', 0)) >= 75 and min(m.get('peer_counts', {}).values(), default=0) >= 4),
            ('liquidity', 'Thanh khoản xác nhận', m['liquidity'] is not None and m['liquidity'] >= 1.1),
            ('breadth', 'Độ rộng MA20/MA50', m['breadth20'] is not None and m['breadth'] is not None and min(m['breadth20'], m['breadth']) >= 60),
            ('breakout', 'Nhiều mã vượt đỉnh', m['breakout_count'] >= 2 and m['breakout_rate'] >= 15 and m['leader_breakouts'] >= 1),
            ('persistence', 'Duy trì sức mạnh', persistence >= 3 and g['relative_change'] is not None and g['relative_change'] >= 0),
        ]
        g['leadership'] = {'checks': [{'key': k, 'label': label, 'passed': bool(ok)} for k, label, ok in checks], 'persistence': persistence, 'required_sessions': 5}
        if not enough or g['relative_change'] is None or g['breadth_change'] is None:
            g['status'] = 'insufficient'
        elif all(c[2] for c in checks):
            g['status'] = 'leading'
        elif g['relative_change'] < 0 and g['breadth_change'] < 0:
            g['status'] = 'weakening'
        elif g['relative_change'] > 0 and g['breadth_change'] > 0:
            g['status'] = 'improving'
        elif ex.get('20', -1) > 0 and ex.get('60', -1) > 0:
            g['status'] = 'outperforming'
        else:
            g['status'] = 'mixed'


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
                valid[t]={d.date().isoformat():(float(r.close),float(r.close*r.volume),float(getattr(r,'high',float('nan'))),float(r.volume)) for d,r in f.iterrows()}
        def snapshot(day):
            k=dates.index(day); results={}; above=[]; above20=[]; volumes=[]; breakouts=[]; breakouts60=[]; top=sorted(valid, key=lambda t: next(m['value'] for m in members if m['ticker']==t), reverse=True)[:3]
            for t,series in valid.items():
                if day not in series:continue
                close=series[day][0]
                hist=[v[0] for d,v in sorted(series.items()) if d<=day]
                if len(hist)>=50:above.append(close>sum(hist[-50:])/50)
                if len(hist)>=20:above20.append(close>sum(hist[-20:])/20)
                past=[v for d,v in sorted(series.items()) if d<=day]
                for window, hits in ((20, breakouts), (60, breakouts60)):
                    # A breakout in the last five sessions, still above its broken resistance.
                    for j in range(max(window, len(past)-5), len(past)):
                        if not all(isfinite(v[2]) for v in past[j-window:j]):
                            continue
                        resistance=max(v[2] for v in past[j-window:j])
                        volume=sum(v[3] for v in past[j-20:j])/20
                        if past[j][0] > resistance and volume > 0 and past[j][3] >= 1.2*volume and close > resistance:
                            hits.append(t); break
                for n in (5,20,21,60,63):
                    if k>=n and dates[k-n] in series and series[dates[k-n]][0]>0:
                        results.setdefault(n,[]).append((close/series[dates[k-n]][0]-1)*100)
                vals=[v[1] for d,v in sorted(series.items()) if d<=day][-25:]
                if len(vals)==25 and sum(vals[:20])>0:volumes.append((sum(vals[-5:])/5)/(sum(vals[:20])/20))
            r={str(n):round(median(v),2) for n,v in results.items() if v}
            rel=r.get('21')
            if rel is not None and k>=21 and index[dates[k-21]]>0:rel-= (index[day]/index[dates[k-21]]-1)*100
            excess={str(n):round(r[str(n)]-(index[day]/index[dates[k-n]]-1)*100,2) for n in (20,60) if str(n) in r and k>=n and index[dates[k-n]]>0}
            return {'date':day,'returns':r,'excess':excess,'counts':{str(n):len(v) for n,v in results.items()},
                    'breadth20':round(100*sum(above20)/len(above20),1) if above20 else None,
                    'breakout_count':len(breakouts),'breakout60_count':len(breakouts60),
                    'breakout_rate':round(100*len(breakouts)/len(above),1) if above else 0,
                    'breakout_tickers':breakouts,'breakout60_tickers':breakouts60,
                    'leader_breakouts':len(set(top)&set(breakouts)),
                    'relative':round(rel,2) if rel is not None else None,
                    'breadth':round(100*sum(above)/len(above),1) if above else None,
                    'liquidity':round(median(volumes),2) if volumes else None,'count':len(above)}
        history=[snapshot(d) for d in dates[-21:]]
        now=history[-1]; before=history[-6] if len(history)>=6 else {}
        delta=round(now['excess']['20']-before['excess']['20'],2) if now['excess'].get('20') is not None and before.get('excess',{}).get('20') is not None else None
        breadth_delta=round(now['breadth']-before['breadth'],1) if now['breadth'] is not None and before.get('breadth') is not None else None
        out.append({'id':key,**g,'tickers':None,'members':members,'metrics':now,'history':history,
                    'relative_change':delta,'breadth_change':breadth_delta,'status':'insufficient'})
    rank_and_classify(out)
    return {'groups':out,'as_of':dates[-1], 'method':'Lợi nhuận trung vị; so VN-Index theo cùng phiên. Chỉ đo mã đủ thanh khoản, đủ lịch sử và có giá phiên này. Lịch sử dùng cùng tập mã hiện tại, không phải kiểm định quá khứ.'}
