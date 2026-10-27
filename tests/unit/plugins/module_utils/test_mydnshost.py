# -*- coding: utf-8 -*-
# MIT License (see LICENCE)

from __future__ import absolute_import, division, print_function
__metaclass__ = type

import pytest

from ansible_collections.mydnshost.dns.plugins.module_utils.mydnshost import (
    build_request,
    find_domain,
    format_records,
    normalise_content,
    parse_value,
    plan_changes,
    predict_records,
    relative_name,
)


def rec(id, content, ttl=86400, priority=None, name='www', type='A'):
    return {'id': id, 'name': name, 'type': type, 'content': content, 'ttl': ttl, 'priority': priority}


def contents(records):
    return sorted(r['content'] for r in records)


class TestFindDomain:
    def test_longest_match(self):
        assert find_domain(['example.com', 'bar.example.com'], 'foo.bar.example.com') == 'bar.example.com'

    def test_label_boundary(self):
        assert find_domain(['r.example.com'], 'foo.bar.example.com') is None

    def test_apex_and_trailing_dot(self):
        assert find_domain(['Example.com'], 'EXAMPLE.com.') == 'example.com'

    def test_no_match(self):
        assert find_domain(['example.org'], 'www.example.com') is None


class TestRelativeName:
    @pytest.mark.parametrize('name', ['', '@', '.', 'example.com', 'example.com.', 'EXAMPLE.COM'])
    def test_apex(self, name):
        assert relative_name(name, 'example.com') == ''

    @pytest.mark.parametrize('name', ['www', 'www.example.com', 'www.example.com.', 'WWW.Example.Com'])
    def test_subdomain(self, name):
        assert relative_name(name, 'example.com') == 'www'

    def test_wildcard(self):
        assert relative_name('*.example.com', 'example.com') == '*'

    def test_unqualified_outside_domain_is_relative(self):
        assert relative_name('www.example.org', 'example.com') == 'www.example.org'

    def test_qualified_outside_domain_fails(self):
        with pytest.raises(ValueError):
            relative_name('www.example.org.', 'example.com')


class TestNormaliseContent:
    def test_ipv6_forms(self):
        assert normalise_content('AAAA', '2001:0db8:0000::0001') == normalise_content('AAAA', '2001:db8::1')

    def test_invalid_ip_left_alone(self):
        assert normalise_content('A', 'not-an-ip') == 'not-an-ip'

    @pytest.mark.parametrize('rtype', ['CNAME', 'MX', 'NS', 'PTR'])
    def test_hostname_trailing_dot(self, rtype):
        assert normalise_content(rtype, 'Target.Example.com.') == 'target.example.com'

    def test_srv_target(self):
        assert normalise_content('SRV', '5 5060 sip.example.com.') == '5 5060 sip.example.com'

    def test_srv_null_target(self):
        assert normalise_content('SRV', '0 0 .') == '0 0 .'

    def test_svcb_target(self):
        assert normalise_content('HTTPS', 'svc.example.com. alpn=h2') == 'svc.example.com alpn=h2'

    def test_rrclone_target(self):
        assert normalise_content('RRCLONE', '(A,AAAA) www.example.com.') == '(A,AAAA) www.example.com'

    def test_txt_untouched(self):
        assert normalise_content('TXT', ' v=spf1 MX -all. ') == 'v=spf1 MX -all.'


class TestParseValue:
    def test_non_priority(self):
        assert parse_value('A', ' 192.0.2.1 ') == (None, '192.0.2.1')

    def test_txt_with_spaces(self):
        assert parse_value('TXT', '10 green bottles') == (None, '10 green bottles')

    def test_mx(self):
        assert parse_value('MX', '10 mx.example.com') == (10, 'mx.example.com')

    def test_srv(self):
        assert parse_value('SRV', '10 5 5060 sip.example.com') == (10, '5 5060 sip.example.com')

    @pytest.mark.parametrize('value', ['mx.example.com', 'ten mx.example.com', '10'])
    def test_mx_without_priority(self, value):
        with pytest.raises(ValueError):
            parse_value('MX', value)


class TestPlanChanges:
    def test_add_missing(self):
        plan = plan_changes('A', [rec(1, '192.0.2.1')], ['192.0.2.1', '192.0.2.2'])
        assert plan['add'] == [(None, '192.0.2.2')]
        assert plan['update'] == [] and plan['delete'] == []

    def test_nothing_to_do(self):
        plan = plan_changes('A', [rec(1, '192.0.2.1')], ['192.0.2.1'])
        assert plan == dict(add=[], update=[], delete=[])

    def test_non_exclusive_keeps_others(self):
        plan = plan_changes('A', [rec(1, '192.0.2.1'), rec(2, '192.0.2.9')], ['192.0.2.1'])
        assert plan['delete'] == []

    def test_exclusive_removes_others(self):
        existing = [rec(1, '192.0.2.1'), rec(2, '192.0.2.9')]
        plan = plan_changes('A', existing, ['192.0.2.1'], exclusive=True)
        assert plan['delete'] == [existing[1]]

    def test_exclusive_removes_duplicates(self):
        existing = [rec(1, '192.0.2.1'), rec(2, '192.0.2.1')]
        plan = plan_changes('A', existing, ['192.0.2.1'], exclusive=True)
        assert plan['delete'] == [existing[1]]

    def test_ttl_update(self):
        existing = [rec(1, '192.0.2.1', ttl=300), rec(2, '192.0.2.2', ttl=60)]
        plan = plan_changes('A', existing, ['192.0.2.1', '192.0.2.2'], ttl=60)
        assert plan['update'] == [existing[0]]

    def test_ttl_unset_leaves_existing(self):
        plan = plan_changes('A', [rec(1, '192.0.2.1', ttl=300)], ['192.0.2.1'])
        assert plan['update'] == []

    def test_ipv6_equivalent_forms(self):
        plan = plan_changes('AAAA', [rec(1, '2001:db8::1', type='AAAA')], ['2001:0db8:0:0::1'], exclusive=True)
        assert plan == dict(add=[], update=[], delete=[])

    def test_cname_trailing_dot(self):
        plan = plan_changes('CNAME', [rec(1, 'target.example.com', type='CNAME')], ['target.example.com.'])
        assert plan == dict(add=[], update=[], delete=[])

    def test_mx_priority_is_part_of_identity(self):
        existing = [rec(1, 'mx.example.com', priority=10, type='MX')]
        plan = plan_changes('MX', existing, ['20 mx.example.com'], exclusive=True)
        assert plan['add'] == [(20, 'mx.example.com')]
        assert plan['delete'] == existing

    def test_duplicate_desired_values(self):
        plan = plan_changes('A', [], ['192.0.2.1', '192.0.2.1'])
        assert plan['add'] == [(None, '192.0.2.1')]

    def test_absent_specific(self):
        existing = [rec(1, '192.0.2.1'), rec(2, '192.0.2.2')]
        plan = plan_changes('A', existing, ['192.0.2.2', '192.0.2.3'], state='absent')
        assert plan == dict(add=[], update=[], delete=[existing[1]])

    def test_absent_all(self):
        existing = [rec(1, '192.0.2.1'), rec(2, '192.0.2.2')]
        plan = plan_changes('A', existing, None, state='absent')
        assert plan['delete'] == existing

    def test_absent_nothing_there(self):
        assert plan_changes('A', [], ['192.0.2.1'], state='absent') == dict(add=[], update=[], delete=[])


class TestBuildRequest:
    def test_request(self):
        existing = [rec(1, '192.0.2.1', ttl=60), rec(2, '192.0.2.9')]
        plan = plan_changes('A', existing, ['192.0.2.1', '192.0.2.2'], ttl=300, exclusive=True)
        assert build_request('www', 'A', plan, 300) == [
            {'id': 2, 'delete': True},
            {'id': 1, 'ttl': 300},
            {'name': 'www', 'type': 'A', 'content': '192.0.2.2', 'ttl': 300},
        ]

    def test_priority_and_no_ttl(self):
        plan = plan_changes('MX', [], ['10 mx.example.com'])
        assert build_request('', 'MX', plan) == [{'name': '', 'type': 'MX', 'content': 'mx.example.com', 'priority': 10}]


class TestPredictRecords:
    def test_predict(self):
        existing = [rec(1, '192.0.2.1', ttl=60), rec(2, '192.0.2.9')]
        plan = plan_changes('A', existing, ['192.0.2.1', '192.0.2.2'], ttl=300, exclusive=True)
        after = predict_records('www', 'A', existing, plan, 300)
        assert contents(after) == ['192.0.2.1', '192.0.2.2']
        assert all(r['ttl'] == 300 for r in after)
        assert existing[0]['ttl'] == 60


class TestFormatRecords:
    def test_format(self):
        records = [rec(1, 'mx.example.com', priority=10, type='MX', name=''), rec(2, '192.0.2.1', ttl=None)]
        assert format_records('example.com', records) == (
            'example.com. 86400 IN MX 10 mx.example.com\n'
            'www.example.com. IN A 192.0.2.1\n'
        )

    def test_empty(self):
        assert format_records('example.com', []) == ''
