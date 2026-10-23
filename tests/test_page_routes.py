from html.parser import HTMLParser
from pathlib import Path
from threading import Thread
from http.server import ThreadingHTTPServer
from urllib.request import urlopen
from urllib.error import HTTPError
import pytest
from src.web.page_routes import PAGE_PATHS, is_page_path, write_page_aliases
from src.web.server import Handler, STATIC


class MenuLinks(HTMLParser):
    def __init__(self):
        super().__init__(); self.paths=[]; self.base=None
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag=='base': self.base=a.get('href')
        if tag=='a' and 'tab' in a.get('class','').split(): self.paths.append(a['href'])


def test_menu_routes_and_export_aliases_match(tmp_path):
    html=(STATIC/'index.html').read_text(encoding='utf-8')
    menu=MenuLinks();menu.feed(html)
    assert menu.paths==list(PAGE_PATHS)
    assert len(set(menu.paths))==9 and menu.base=='/'
    (tmp_path/'index.html').write_text(html,encoding='utf-8')
    write_page_aliases(tmp_path)
    for route in PAGE_PATHS:
        assert (tmp_path/(route[1:]+'.html')).read_bytes()==(tmp_path/'index.html').read_bytes()
        assert is_page_path(route) and is_page_path(route+'/') and is_page_path(route+'.html')
    assert not is_page_path('/not-a-page') and not is_page_path('/api/news')


def test_direct_page_refresh_routes_work_on_local_server():
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        root=f'http://127.0.0.1:{server.server_port}'
        expected=(STATIC/'index.html').read_bytes()
        for path in ('/', *PAGE_PATHS):
            with urlopen(root+path) as response:
                assert response.status==200 and response.read()==expected
        with pytest.raises(HTTPError) as error:
            urlopen(root+'/unknown-page')
        assert error.value.code==404
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
