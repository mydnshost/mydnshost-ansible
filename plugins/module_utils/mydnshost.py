# -*- coding: utf-8 -*-
# MIT License (see LICENCE)

from __future__ import absolute_import, division, print_function
__metaclass__ = type

import ipaddress
import traceback

from ansible.module_utils.basic import env_fallback, missing_required_lib

try:
    from mydnshost.api import MyDNSHostAPI, UserKeyAuthenticator, DomainKeyAuthenticator
    # Re-exported for modules.
    from mydnshost.api import MyDnsApiException  # noqa: F401 pylint: disable=unused-import
    HAS_MYDNSHOST = True
    MYDNSHOST_IMPORT_ERROR = None
except ImportError:
    HAS_MYDNSHOST = False
    MYDNSHOST_IMPORT_ERROR = traceback.format_exc()

    class MyDnsApiException(Exception):
        pass


DEFAULT_API_URL = 'https://api.mydnshost.co.uk/'

# Record types that can be managed (SOA is handled by the domain itself).
RECORD_TYPES = ['A', 'AAAA', 'CAA', 'CNAME', 'DS', 'HTTPS', 'MX', 'NS', 'PTR', 'RRCLONE', 'SRV', 'SSHFP', 'SVCB', 'TLSA', 'TXT']

# Types that require a priority (see Bind::PRIORITY_RRS in api-base).
PRIORITY_TYPES = ('MX', 'SRV', 'SVCB', 'HTTPS')

# Types whose content contains a hostname, and where in the content it sits. The API strips a trailing dot from these.
TARGET_POSITION = {
    'CNAME': None,
    'MX': None,
    'NS': None,
    'PTR': None,
    'SRV': -1,
    'RRCLONE': -1,
    'SVCB': 0,
    'HTTPS': 0,
}


def auth_argument_spec():
    return dict(
        api_url=dict(type='str', fallback=(env_fallback, ['MYDNSHOST_API_URL'])),
        auth_user=dict(type='str', fallback=(env_fallback, ['MYDNSHOST_AUTH_USER'])),
        auth_key=dict(type='str', no_log=True, fallback=(env_fallback, ['MYDNSHOST_AUTH_KEY'])),
        domain_key=dict(type='str', no_log=True, fallback=(env_fallback, ['MYDNSHOST_DOMAIN_KEY'])),
        timeout=dict(type='int', default=30),
    )


def build_client(module, domain=None):
    """Creates an API client from the module's auth parameters, failing the module if that isn't possible."""
    if not HAS_MYDNSHOST:
        module.fail_json(msg=missing_required_lib('mydnshost', url='https://github.com/mydnshost/mydnshost-python-api'),
                         exception=MYDNSHOST_IMPORT_ERROR)

    params = module.params
    if params['auth_key'] and params['domain_key']:
        module.fail_json(msg='auth_key and domain_key are mutually exclusive.')

    if params['domain_key']:
        if not domain:
            module.fail_json(msg='domain is required when authenticating with domain_key.')
        auth = DomainKeyAuthenticator(domain, params['domain_key'])
    elif params['auth_user'] and params['auth_key']:
        auth = UserKeyAuthenticator(params['auth_user'], params['auth_key'])
    else:
        module.fail_json(msg='No credentials supplied: set auth_user and auth_key, or domain and domain_key.')

    api = MyDNSHostAPI(base_url=params['api_url'] or DEFAULT_API_URL, auth=auth)
    api.set_timeout(params['timeout'])
    return api


def normalise_domain(domain):
    return domain.strip().rstrip('.').lower()


def find_domain(domains, fqdn):
    """
    Finds the most specific domain that contains the given name, or None.

    Matches must end on a label boundary, so 'foo.bar.example.com' matches 'bar.example.com' and 'example.com' (and
    returns the former), but not 'r.example.com'.
    """
    fqdn = '.' + normalise_domain(fqdn)
    matches = [d for d in domains if fqdn.endswith('.' + normalise_domain(d))]
    return normalise_domain(max(matches, key=len)) if matches else None


def relative_name(name, domain):
    """
    Converts a record name to one relative to the domain ('' for the apex).

    Names ending in a dot are treated as fully qualified and must be inside the domain. Other names are treated as
    fully qualified if they end with the domain, and as relative otherwise.
    """
    name = name.strip().lower()
    if name in ('', '@', '.'):
        return ''

    fqdn = name.endswith('.')
    name = name.rstrip('.')
    if name == domain:
        return ''
    if name.endswith('.' + domain):
        return name[:-len(domain) - 1]
    if fqdn:
        raise ValueError('%s. is not inside domain %s' % (name, domain))
    return name


def _canonical_target(target):
    if target != '.':
        target = target.rstrip('.')
    return target.lower()


def normalise_content(rtype, content):
    """Returns a canonical form of the content for comparison purposes."""
    content = content.strip()

    if rtype in ('A', 'AAAA'):
        try:
            return ipaddress.ip_address(content).compressed
        except ValueError:
            return content

    if rtype in TARGET_POSITION:
        position = TARGET_POSITION[rtype]
        if position is None:
            return _canonical_target(content)
        bits = content.split(' ')
        bits[position] = _canonical_target(bits[position])
        return ' '.join(bits)

    return content


def parse_value(rtype, value):
    """
    Splits a value into (priority, content).

    For types that take a priority, the value is written zone-file style with the priority first (eg '10 mx.example.com').
    Other types have a priority of None.
    """
    value = str(value).strip()
    if rtype not in PRIORITY_TYPES:
        return None, value

    bits = value.split(None, 1)
    if len(bits) != 2 or not bits[0].isdigit():
        raise ValueError('%s values must start with a numeric priority, eg "10 %s", got: "%s"' % (rtype, bits[-1] if bits else 'target', value))
    return int(bits[0]), bits[1].strip()


def record_key(rtype, priority, content):
    return (priority if rtype in PRIORITY_TYPES else None, normalise_content(rtype, content))


def plan_changes(rtype, existing, values, ttl=None, state='present', exclusive=False):
    """
    Works out what needs to change to get from the existing records for a name/type to the desired state.

    Args:
        rtype: The record type.
        existing: List of existing record dicts from the API (with id, content, priority, ttl).
        values: List of desired values (see parse_value), or None.
        ttl: Desired TTL, or None to leave TTLs alone.
        state: 'present' or 'absent'.
        exclusive: When present, whether to remove existing values that are not listed.

    Returns:
        A dict with 'add' (list of (priority, content)), 'update' (list of existing records whose TTL needs changing)
        and 'delete' (list of existing records to remove).
    """
    desired = {}
    for value in values or []:
        priority, content = parse_value(rtype, value)
        desired.setdefault(record_key(rtype, priority, content), (priority, content))

    existing_by_key = {}
    for record in existing:
        existing_by_key.setdefault(record_key(rtype, record.get('priority'), record['content']), []).append(record)

    add, update, delete = [], [], []

    if state == 'absent':
        for key, records in existing_by_key.items():
            if values is None or key in desired:
                delete.extend(records)
        return dict(add=add, update=update, delete=delete)

    for key, wanted in desired.items():
        records = existing_by_key.get(key)
        if not records:
            add.append(wanted)
            continue
        if ttl is not None and records[0].get('ttl') != ttl:
            update.append(records[0])
        if exclusive:
            delete.extend(records[1:])

    if exclusive:
        for key, records in existing_by_key.items():
            if key not in desired:
                delete.extend(records)

    return dict(add=add, update=update, delete=delete)


def has_changes(plan):
    return bool(plan['add'] or plan['update'] or plan['delete'])


def build_request(name, rtype, plan, ttl=None):
    """Builds the 'records' list for a POST to domains/{domain}/records."""
    records = [{'id': r['id'], 'delete': True} for r in plan['delete']]
    records += [{'id': r['id'], 'ttl': ttl} for r in plan['update']]
    for priority, content in plan['add']:
        record = {'name': name, 'type': rtype, 'content': content}
        if priority is not None:
            record['priority'] = priority
        if ttl is not None:
            record['ttl'] = ttl
        records.append(record)
    return records


def predict_records(name, rtype, existing, plan, ttl=None):
    """Returns the expected record set after applying the plan (new records have no id)."""
    deleted = set(id(r) for r in plan['delete'])
    updated = set(id(r) for r in plan['update'])
    result = []
    for record in existing:
        if id(record) in deleted:
            continue
        if id(record) in updated:
            record = dict(record, ttl=ttl)
        result.append(record)
    for priority, content in plan['add']:
        result.append({'name': name, 'type': rtype, 'content': content, 'priority': priority, 'ttl': ttl})
    return result


def format_records(domain, records):
    """Formats records as zone-file style lines, for diff output."""
    lines = []
    for r in records:
        fqdn = '%s.%s.' % (r['name'], domain) if r['name'] else '%s.' % domain
        ttl = '' if r.get('ttl') is None else '%s ' % r['ttl']
        priority = '' if r.get('priority') is None else '%s ' % r['priority']
        lines.append('%s %sIN %s %s%s' % (fqdn, ttl, r['type'], priority, r['content']))
    return '\n'.join(sorted(lines)) + '\n' if lines else ''
