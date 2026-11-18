"""Message center (message_center.py): posting through the registry, the
top-bar bell, the Messages page, the activity log, safe text and pictures."""
import base64
import io
import json

import pytest

from functions import InvalidArguments
from message_center import IMAGE_MAX_PX, check_image, render_text


def _png(w, h, fmt='PNG'):
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (w, h), (200, 30, 30)).save(buf, fmt)
    kind = {'PNG': 'png', 'JPEG': 'jpeg', 'GIF': 'gif'}[fmt]
    return f'data:image/{kind};base64,' + base64.b64encode(buf.getvalue()).decode()


def _clear_messages():
    from sqlalchemy.exc import OperationalError
    from app import Message, db
    try:
        Message.query.delete()
        db.session.commit()
    except OperationalError:   # another test's app fixture dropped the tables
        db.session.rollback()
    db.session.expunge_all()


@pytest.fixture
def center(loaded_modules, monkeypatch):
    from app import db
    db.create_all()
    _clear_messages()
    c = loaded_modules.core.message_center
    events = []
    monkeypatch.setattr(c, '_log', lambda action, category, details: events.append(
        (action, category, details)))
    c.events = events
    yield c
    _clear_messages()


@pytest.fixture
def owner(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    return client


# --- posting ---------------------------------------------------------------- #

def test_core_declares_the_message_functions(loaded_modules):
    functions = loaded_modules.functions
    assert functions.provider('messages.post') == 'core'
    assert functions.provider('messages.unread_count') == 'core'
    assert functions.get('messages.post').read_only is False


def test_post_through_the_registry_and_deduplicate(center, loaded_modules):
    call = loaded_modules.functions.call
    first = call('messages.post', source='documents', text='Passport expires', key='doc-1')
    assert first['created'] is True and isinstance(first['id'], int)
    assert call('messages.unread_count') == 1
    again = call('messages.post', source='documents', text='Passport expires', key='doc-1')
    assert again == {'id': first['id'], 'created': False}
    # Read or deleted: the same key still does not come back.
    center.mark_read(first['id'])
    assert call('messages.post', source='documents', text='x', key='doc-1')['created'] is False
    center.delete(first['id'])
    assert call('messages.post', source='documents', text='x', key='doc-1')['created'] is False
    # Another source may use the same key; no key means always a new message.
    assert call('messages.post', source='other', text='x', key='doc-1')['created'] is True
    a = call('messages.post', source='documents', text='Same text')
    b = call('messages.post', source='documents', text='Same text')
    assert a['id'] != b['id']


@pytest.mark.parametrize('kwargs,fragment', [
    ({'source': 'Bad Source', 'text': 'x'}, 'source'),
    ({'source': 'mod', 'text': 'x', 'level': 'loud'}, 'level'),
    ({'source': 'mod', 'text': 'x', 'image': 'https://example.com/a.png'}, 'image'),
    ({'source': 'mod', 'text': 'x', 'image': '/static/../app.py'}, 'image'),
])
def test_post_refuses_bad_arguments(center, kwargs, fragment):
    with pytest.raises(InvalidArguments, match=fragment):
        center.post(**kwargs)
    with pytest.raises(InvalidArguments, match='text'):
        center.post('mod', '')
    with pytest.raises(InvalidArguments, match='text'):
        center.post('mod', 'x' * 1001)


def test_pictures_up_to_the_size_limit(center):
    ok = _png(IMAGE_MAX_PX, IMAGE_MAX_PX)
    assert check_image(ok) == ok
    assert check_image(_png(16, 16, 'JPEG'))
    assert check_image('/static/icons/icon-192.png') == '/static/icons/icon-192.png'
    with pytest.raises(InvalidArguments, match='64x64'):
        check_image(_png(IMAGE_MAX_PX + 1, 10))
    with pytest.raises(InvalidArguments, match='does not match'):
        check_image(_png(10, 10).replace('image/png', 'image/gif'))
    with pytest.raises(InvalidArguments, match='not a picture'):
        check_image('data:image/png;base64,' + base64.b64encode(b'not an image').decode())
    msg, _ = center.post('mod', 'With a picture', image=ok)
    assert msg.image == ok


def test_text_is_escaped_and_links_are_safe():
    html = str(render_text('<b>Hi</b> 📄 [open](/documents/12) and [site](https://aeat.es/x) '
                           '[bad](javascript:alert(1)) [proto](//evil.com/x)'))
    assert '&lt;b&gt;Hi&lt;/b&gt; 📄' in html
    assert '<a href="/documents/12">open</a>' in html
    assert '<a href="https://aeat.es/x" target="_blank" rel="noopener noreferrer">site</a>' in html
    assert 'href="javascript' not in html and 'href="//evil' not in html
    assert '[proto](//evil.com/x)' in html
    assert str(render_text('[x"><script>](/a)')) .count('<script>') == 0


# --- reading, logging --------------------------------------------------------- #

def test_read_unread_delete_are_logged_with_the_id(center):
    msg, _ = center.post('documents', 'Contract expires')
    center.mark_read(msg.id)
    center.mark_read(msg.id)                 # already read: no second entry
    center.mark_unread(msg.id)
    center.delete(msg.id)
    assert [e[0] for e in center.events] == ['message_posted', 'message_read',
                                            'message_unread', 'message_deleted']
    assert all(e[1] == 'messages' and e[2]['id'] == msg.id for e in center.events)
    assert center.get(msg.id) is None and center.unread_count() == 0
    assert center.history()[1] == 0


def test_mark_all_read(center):
    for i in range(3):
        center.post('mod', f'Message {i}')
    assert center.mark_all_read() == 3
    assert center.unread_count() == 0
    assert [e[0] for e in center.events].count('message_read') == 3


# --- pages ----------------------------------------------------------------- #

def test_bell_lists_unread_messages_with_mark_as_read(center, owner):
    msg, _ = center.post('documents', 'Passport [expires](/documents/7) soon', level='warning')
    html = owner.get('/messages').get_data(as_text=True)
    assert 'data-msg-bell' in html and 'class="msg-badge" data-msg-count >1<' in html.replace('\n', '')
    assert f'/messages/{msg.id}/read' in html and 'Mark as read' in html
    assert '<a href="/documents/7">expires</a>' in html
    assert 'id="message-%d"' % msg.id in html            # history entry, the log links here


def test_mark_read_from_the_bell_returns_json(center, owner):
    msg, _ = center.post('mod', 'One')
    center.post('mod', 'Two')
    resp = owner.post(f'/messages/{msg.id}/read', headers={'Accept': 'application/json'})
    assert resp.get_json() == {'id': msg.id, 'read': True, 'unread_count': 1}
    resp = owner.post('/messages/99999/read', headers={'Accept': 'application/json'})
    assert resp.status_code == 404


def test_history_page_unread_again_and_delete(center, owner):
    msg, _ = center.post('mod', 'History entry')
    owner.post(f'/messages/{msg.id}/read', data={'next': '/messages'})
    html = owner.get('/messages?show=unread').get_data(as_text=True)
    assert 'History entry' not in html.split('<h1>Messages</h1>')[1]
    resp = owner.post(f'/messages/{msg.id}/unread', data={'next': '//evil.com'})
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/messages')
    assert center.unread_count() == 1
    owner.post(f'/messages/{msg.id}/delete', data={'next': '/messages'})
    assert 'History entry' not in owner.get('/messages').get_data(as_text=True)
    owner.post('/messages/read-all')


def test_pages_require_login(loaded_modules, client):
    assert client.get('/messages').status_code in (302, 401)
    assert client.post('/messages/1/read').status_code in (302, 401)


def test_logs_page_links_message_entries(loaded_modules, owner):
    from app import _message_log_link, app
    with app.test_request_context():
        link = _message_log_link({'category': 'messages',
                                  'details': json.dumps({'id': 12, 'source': 'documents'})})
        assert link == {'id': 12, 'url': '/messages#message-12'}
        assert _message_log_link({'category': 'invoice', 'details': '{"id": 3}'}) is None
        assert _message_log_link({'category': 'messages', 'details': 'not json'}) is None


def test_messages_page_is_in_the_system_group(loaded_modules, owner):
    system = [e for e in loaded_modules.get_full_nav()
              if e.get('type') == 'dropdown' and e['label'] == 'System'][0]
    assert system['items'][0] == {'label': 'Messages', 'endpoint': 'messages_index'}
    top_level = [e['endpoint'] for e in loaded_modules.get_full_nav() if e.get('type') == 'link']
    assert 'messages_index' not in top_level
    html = owner.get('/messages').get_data(as_text=True)
    assert 'href="/messages" class="side-sublink"' in html and 'aria-current="page"' in html
