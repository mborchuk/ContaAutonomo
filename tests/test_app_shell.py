"""App shell (templates/base.html): left sidebar, its footer, the top bar.

The sidebar lists Dashboard and the navigation of get_full_nav(); Settings,
the dark-mode switch and Logout sit in the sidebar footer. Modules put
widgets (search, notifications, …) in the top bar through
BaseModule.get_header_widgets().
"""
import re

import pytest
from jinja2 import ChoiceLoader, DictLoader


def _get(client, url):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    resp = client.get(url)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _between(html, start, end):
    a = html.index(start)
    return html[a:html.index(end, a)]


def _primary_nav(html):
    return _between(html, 'aria-label="Primary"', '</nav>')


def _sidebar_footer(html):
    return _between(html, 'class="sidebar-footer"', '</aside>')


def _current(fragment):
    """Labels of links marked aria-current="page" in an HTML fragment."""
    return re.findall(r'aria-current="page"[^>]*>\s*(?:<span[^>]*>[^<]*</span>)?'
                      r'(?:<span class="side-label">)?([^<]+)<', fragment)


@pytest.fixture
def widgets(app, loaded_modules, monkeypatch):
    """Let a test give one loaded module header widgets with inline templates."""
    loader = ChoiceLoader([DictLoader({
        'test_search_header_widget.html':
            '<input type="search" class="test-search" placeholder="{{ data.hint }}">',
        'test_bell_header_widget.html': '<button class="test-bell">bell</button>',
    }), app.jinja_env.loader])
    monkeypatch.setattr(app.jinja_env, 'loader', loader)
    module = loaded_modules.modules['expenses']

    def give(entries):
        monkeypatch.setattr(module, 'get_header_widgets', lambda: entries)
    return give


def test_sidebar_lists_navigation_and_footer(loaded_modules, client):
    html = _get(client, '/invoices')
    nav = _primary_nav(html)
    assert '<aside class="sidebar"' in html
    assert '>Dashboard<' in nav
    assert '>Invoices<' in nav and '>Expenses<' in nav
    assert '>Settings<' not in nav                # moved to the footer
    footer = _sidebar_footer(html)
    assert '>Settings<' in footer
    assert 'role="switch"' in footer and 'id="theme-toggle"' in footer
    assert 'action="/logout"' in footer and 'method="POST"' in footer
    assert 'class="theme-toggle"' not in html     # old floating button is gone


def test_current_page_marked_and_its_group_open(loaded_modules, client):
    nav = _primary_nav(_get(client, '/invoices'))
    assert _current(nav) == ['All Invoices']
    group = _between(nav, 'data-group="Invoices"', '</ul>')
    assert 'is-open' in nav[nav.rindex('<li', 0, nav.index('data-group="Invoices"')):]
    assert 'aria-expanded="true"' in group
    system = _between(nav, 'data-group="System"', '</button>')
    assert 'aria-expanded="false"' in system


def test_module_page_marks_its_entry(loaded_modules, client):
    assert _current(_primary_nav(_get(client, '/expenses/'))) == ['Expenses']


def test_module_sub_page_marks_its_module_entry(loaded_modules, client):
    # /expenses/create has no nav entry of its own: the blueprint's entry is marked.
    assert _current(_primary_nav(_get(client, '/expenses/create'))) == ['Expenses']


def test_core_detail_page_marks_its_parent(loaded_modules, client):
    html = _get(client, '/customers/create')
    assert _current(_primary_nav(html)) == []
    assert _current(_sidebar_footer(html)) == ['Settings']


def test_topbar_always_shows_the_message_bell(loaded_modules, client):
    # The core message bell (message_center.py) sits in the top bar's right
    # slot, so the bar shows for a signed-in owner even without module widgets.
    html = _get(client, '/invoices')
    assert 'topbar--empty' not in html
    end = _between(html, 'class="topbar-slot topbar-end"', '</header>')
    assert 'data-msg-bell' in end
    assert 'aria-controls="sidebar"' in html      # phone/tablet menu button


def test_header_widgets_render_in_their_slot(loaded_modules, client, widgets):
    widgets([
        {'id': 'bell', 'template': 'test_bell_header_widget.html'},   # right by default
        {'id': 'search', 'template': 'test_search_header_widget.html', 'position': 'start',
         'data': {'hint': 'Search <all>'}},
    ])
    html = _get(client, '/invoices')
    assert 'topbar--empty' not in html
    start = _between(html, 'class="topbar-slot topbar-start"', '</div>')
    end = _between(html, 'class="topbar-slot topbar-end"', '</div>')
    assert 'class="test-search"' in start and 'test-bell' not in start
    assert 'class="test-bell"' in end and 'test-search' not in end
    assert 'placeholder="Search &lt;all&gt;"' in start   # data is escaped


def test_failing_widget_module_does_not_break_pages(loaded_modules, client, monkeypatch):
    def boom():
        raise RuntimeError('widget failed')
    monkeypatch.setattr(loaded_modules.modules['expenses'], 'get_header_widgets', boom)
    html = _get(client, '/invoices')
    assert 'data-msg-bell' in html                 # the page and the core bell still render


def test_manager_normalises_and_sorts_widgets(loaded_modules, widgets):
    widgets([
        {'id': 'late', 'template': 'a.html', 'order': 50, 'position': 'start'},
        {'id': 'no-template'},
        'not a dict',
        {'id': 'early', 'template': 'b.html', 'order': 5, 'position': 'middle'},
    ])
    got = [w for w in loaded_modules.get_header_widgets() if w['module_id'] == 'expenses']
    assert [w['id'] for w in got] == ['early', 'late']
    assert [w['position'] for w in got] == ['end', 'start']   # unknown → right
    assert all(w['data'] == {} for w in got)


def test_base_module_has_no_widgets_by_default():
    from contaautonomo.core.module_manager import BaseModule
    assert BaseModule.get_header_widgets(object()) == []
