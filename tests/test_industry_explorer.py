import pandas as pd
from src.web import industry_explorer as ex
from src.database import init_db, connect


def test_members_stay_visible_but_flagged_and_stale_prices_do_not_drive_strength(tmp_path,monkeypatch):
    db=tmp_path/'a.db';init_db(db)
    days=pd.bdate_range('2025-01-01',periods=100)
    frame=pd.DataFrame({'close':range(100,200),'volume':1e8},index=days)
    monkeypatch.setattr(ex,'_load_universe_frames',lambda *a:({'AAA':frame,'BBB':frame,'CCC':frame.iloc[:-1]},dict.fromkeys(['AAA','BBB','CCC'],'HOSE'),{'BBB'}))
    monkeypatch.setattr(ex,'load_industries',lambda *a:{t:{'sector_code':'1','sector':'Test'} for t in ['AAA','BBB','CCC']})
    monkeypatch.setattr(ex,'load_themes',lambda:{'Theme':['AAA','BBB','CCC']})
    with connect(db) as conn:
        conn.execute("INSERT INTO sources(source_id,kind,source_level,enabled) VALUES('x','market_data',2,1)")
        conn.executemany("INSERT INTO index_daily(symbol,trade_date,open,high,low,close,volume,source_id,observed_at) VALUES('VNINDEX',?,100,100,100,100,1,'x','x')",[(d.date().isoformat(),) for d in days])
    out=ex.explore(days[-1].date().isoformat(),db)
    for g in out['groups']:
        assert len(g['members'])==3 and g['metrics']['count']==1
        assert g['status']=='insufficient'
        assert [m['ticker'] for m in g['members'] if m['eligible']]==['AAA']
        assert g['metrics']['returns']['21']==round((199/178-1)*100,2)
        assert len(g['history'])==21


def test_missing_benchmark_does_not_invent_strength(tmp_path):
    db=tmp_path/'a.db';init_db(db)
    assert ex.explore('2026-10-16',db)['groups']==[]


def test_board_reference_and_confirmed_limits(tmp_path, monkeypatch):
    db = tmp_path / 'board.db'
    init_db(db)
    days = pd.bdate_range('2025-01-01', periods=100)
    frame = pd.DataFrame({'close': range(100, 200), 'volume': 1e8}, index=days)
    tickers = ['AAA', 'BBB', 'CCC', 'DDD']
    monkeypatch.setattr(ex, '_load_universe_frames', lambda *a: (dict.fromkeys(tickers, frame), dict.fromkeys(tickers, 'HOSE'), set()))
    monkeypatch.setattr(ex, 'load_industries', lambda *a: {t: {'sector_code': '1', 'sector': 'Test'} for t in tickers})
    monkeypatch.setattr(ex, 'load_themes', lambda: {})
    session = days[-1].date().isoformat()
    refs = {
        'AAA': {'reference': 186, 'ceiling': 199, 'floor': 173},
        'BBB': {'reference': 199, 'ceiling': 212, 'floor': 186},
        'CCC': {'reference': 214, 'ceiling': 228, 'floor': 199},
        'DDD': {'reference': 100, 'ceiling': 107, 'floor': 93},
    }
    monkeypatch.setattr(ex.refprices, 'lookup', lambda *a: {(t, session): r for t, r in refs.items()})
    with connect(db) as conn:
        conn.execute("INSERT INTO sources(source_id,kind,source_level,enabled) VALUES('x','market_data',2,1)")
        conn.executemany("INSERT INTO index_daily(symbol,trade_date,open,high,low,close,volume,source_id,observed_at) VALUES('VNINDEX',?,100,100,100,100,1,'x','x')", [(d.date().isoformat(),) for d in days])
    members = {m['ticker']: m for m in ex.explore(session, db)['groups'][0]['members']}
    assert members['AAA']['limit'] == 'ceiling' and members['AAA']['change'] > 0
    assert members['BBB']['limit'] is None and members['BBB']['change'] == 0
    assert members['CCC']['limit'] == 'floor' and members['CCC']['change'] < 0
    assert all(members[t]['change_basis'] == 'ref' for t in ['AAA', 'BBB', 'CCC'])
    assert members['DDD']['limit'] is None and members['DDD']['change_basis'] == 'close'


def leadership_group(name, kind='industry', excess=5, **changes):
    history=[]
    for i in range(21):
        history.append(dict(returns={'20':8,'60':12}, excess={'20':excess,'60':excess},
                            counts={'20':5,'60':5}, breadth20=80, breadth=80,
                            liquidity=1.3, breakout_count=2, breakout_rate=40,
                            leader_breakouts=1))
    history[-1].update(changes)
    return dict(id=name,kind=kind,history=history,metrics=history[-1],relative_change=1,breadth_change=2)


def test_leadership_requires_all_confirmations_and_positive_absolute_return():
    from copy import deepcopy
    strong=leadership_group('strong',excess=10)
    peers=[leadership_group(str(i),excess=i) for i in range(3)]
    ex.rank_and_classify([strong,*peers])
    assert strong['status']=='leading'
    assert strong['leadership']['persistence']==5
    for key,value in [('returns',{'20':-1,'60':5}),('liquidity',.8),
                      ('breadth20',50),('breakout_count',1),('leader_breakouts',0)]:
        g=leadership_group('weak confirmation',excess=10,**{key:value})
        ex.rank_and_classify([g,*deepcopy(peers)])
        assert g['status']!='leading', key
        assert not all(c['passed'] for c in g['leadership']['checks'])


def test_outperformance_is_distinct_and_short_history_is_not_ranked():
    g=leadership_group('relative only',returns={'20':-1,'60':5})
    g['relative_change']=g['breadth_change']=0
    ex.rank_and_classify([g])
    assert g['status']=='outperforming'
    g['metrics']['counts']['60']=2
    ex.rank_and_classify([g])
    assert g['status']=='insufficient'


def test_themes_do_not_change_industry_rank_and_one_group_cannot_lead():
    g=leadership_group('only industry')
    themes=[leadership_group(str(i),'theme',excess=100+i) for i in range(5)]
    ex.rank_and_classify([g,*themes])
    assert g['metrics']['ranks']['20']==100
    assert g['metrics']['peer_counts']['20']==1
    assert g['status']!='leading'


def test_leadership_needs_three_of_five_strong_sessions():
    g=leadership_group('new spike',excess=-1)
    g['metrics']['excess']={'20':10,'60':10}
    peers=[leadership_group(str(i),excess=i) for i in range(3)]
    ex.rank_and_classify([g,*peers])
    assert g['leadership']['persistence']==1
    assert g['status']!='leading'


def test_breakouts_use_prior_highs_and_prior_volume_without_future_bars(tmp_path,monkeypatch):
    db=tmp_path/'breakout.db';init_db(db)
    days=pd.bdate_range('2025-01-01',periods=100)
    frame=pd.DataFrame({'close':range(100,200),'high':range(101,201),'volume':1e8},index=days)
    frame.loc[days[-1],['close','high','volume']]=[202,203,2e8]
    frames={t:frame.copy() for t in ('AAA','BBB','CCC')}
    # High trading volume alone is not confirmation if the close has not cleared prior highs.
    frames['BBB']['high']=300
    # A price breakout without volume confirmation is excluded.
    frames['CCC']['volume']=1e8
    monkeypatch.setattr(ex,'_load_universe_frames',lambda *a:(frames,dict.fromkeys(frames,'HOSE'),set()))
    monkeypatch.setattr(ex,'load_industries',lambda *a:{t:{'sector_code':'1','sector':'Test'} for t in frames})
    monkeypatch.setattr(ex,'load_themes',lambda:{})
    with connect(db) as conn:
        conn.execute("INSERT INTO sources(source_id,kind,source_level,enabled) VALUES('x','market_data',2,1)")
        conn.executemany("INSERT INTO index_daily(symbol,trade_date,open,high,low,close,volume,source_id,observed_at) VALUES('VNINDEX',?,100,100,100,100,1,'x','x')",[(d.date().isoformat(),) for d in days])
    g=ex.explore(days[-1].date().isoformat(),db)['groups'][0]
    assert g['metrics']['breakout_tickers']==['AAA']
    assert g['metrics']['breakout60_tickers']==['AAA']
    assert g['history'][-2]['breakout_count']==0
    assert g['metrics']['counts']['60']==3
