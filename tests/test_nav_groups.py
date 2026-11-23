"""Navigation groups: tax pages under Taxes, Verifactu under Invoices.

Items in a group follow their optional 'order' key; items without it keep
load order after the ordered ones, and groups keep the order they first
appear in.
"""


def _nav(loaded_modules):
    nav = loaded_modules.get_full_nav()
    links = [e['label'] for e in nav if e['type'] == 'link']
    groups = {e['label']: [i['label'] for i in e['items']]
              for e in nav if e['type'] == 'dropdown'}
    return links, groups


def test_tax_pages_are_grouped_under_taxes(loaded_modules):
    links, groups = _nav(loaded_modules)
    assert groups['Taxes'] == ['Tax Forms', 'Obligations', 'Tax Drafts',
                               'IRPF Estimator', 'RETA Advisor', 'Fiscal Calendar', 'Tax Data']
    assert 'Tax Forms' not in links


def test_system_keeps_system_pages(loaded_modules):
    _, groups = _nav(loaded_modules)
    assert groups['System'] == ['Messages', 'Logs', 'Scheduled Tasks', 'Bank Import']


def test_verifactu_is_under_invoices(loaded_modules):
    _, groups = _nav(loaded_modules)
    assert groups['Invoices'][:2] == ['All Invoices', 'Create Invoice']
    assert 'Verifactu' in groups['Invoices']


def test_order_sorts_within_a_group_only(loaded_modules, monkeypatch):
    module = loaded_modules.modules['documents']
    original = module.nav_items
    monkeypatch.setattr(type(module), 'nav_items', property(lambda self: [
        {'label': 'Unordered', 'endpoint': 'documents.documents_index', 'group': 'Zeta'},
        {'label': 'Second', 'endpoint': 'documents.documents_index', 'group': 'Zeta', 'order': 2},
        {'label': 'First', 'endpoint': 'documents.documents_index', 'group': 'Zeta', 'order': 1},
    ] + original))
    nav = loaded_modules.get_full_nav()
    labels = [e['label'] for e in nav]
    groups = {e['label']: [i['label'] for i in e['items']] for e in nav if e['type'] == 'dropdown'}
    assert groups['Zeta'] == ['First', 'Second', 'Unordered']
    assert labels.index('Zeta') < labels.index('Taxes')   # group order unchanged by 'order'
