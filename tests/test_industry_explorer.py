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
