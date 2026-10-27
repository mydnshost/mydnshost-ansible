#!/usr/bin/python
# -*- coding: utf-8 -*-
# MIT License (see LICENCE)

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: record
short_description: Manage DNS records on MyDNSHost
description:
  - Manages the set of records for a name and type on a MyDNSHost domain.
  - All changes for a task are sent in a single request, which the API applies atomically.
author:
  - Shane Mc Cormack (@ShaneMcC)
extends_documentation_fragment:
  - mydnshost.dns.auth
options:
  name:
    description:
      - Name of the record, either fully qualified or relative to O(domain).
      - Use V(@) or an empty string for the domain apex.
      - Names ending in a dot are always treated as fully qualified.
    type: str
    required: true
  domain:
    description:
      - The domain (zone) the record belongs to.
      - If not set, the most specific domain on the account that contains O(name) is used.
      - Required when authenticating with O(domain_key).
    type: str
  type:
    description:
      - The record type.
    type: str
    required: true
    choices: [A, AAAA, CAA, CNAME, DS, HTTPS, MX, NS, PTR, RRCLONE, SRV, SSHFP, SVCB, TLSA, TXT]
  values:
    description:
      - The record values.
      - For V(MX), V(SRV), V(SVCB) and V(HTTPS) records, each value starts with the priority,
        zone-file style, for example V(10 mail.example.com) or V(10 5 5060 sip.example.com).
      - TXT values are given without surrounding quotes.
      - Required when O(state=present).
      - When O(state=absent) and this is not set, all records for O(name) and O(type) are removed.
    type: list
    elements: str
    aliases: [value, content]
  ttl:
    description:
      - TTL for the records, in seconds.
      - Applied to new records, and to existing records matching O(values).
      - If not set, new records use the domain default and existing TTLs are left alone.
    type: int
  exclusive:
    description:
      - When O(state=present), remove any other values for this O(name) and O(type),
        so the record set matches O(values) exactly.
    type: bool
    default: false
  state:
    description:
      - Whether the values should be present or absent.
    type: str
    choices: [present, absent]
    default: present
notes:
  - "This talks to the API from wherever the task runs, so is normally used with C(delegate_to: localhost)."
  - Changing a name between a CNAME and other types needs the old records removed in a separate task first.
attributes:
  check_mode:
    description: Can run in check_mode and return changed status prediction without modifying target.
    support: full
  diff_mode:
    description: Will return details on what has changed (or possibly needs changing in check_mode), when in diff mode.
    support: full
'''

EXAMPLES = r'''
- name: Point www at two addresses, removing any others
  mydnshost.dns.record:
    name: www.example.com
    type: A
    values:
      - 192.0.2.10
      - 192.0.2.11
    ttl: 300
    exclusive: true
  delegate_to: localhost

- name: Set mail servers
  mydnshost.dns.record:
    domain: example.com
    name: "@"
    type: MX
    values:
      - 10 mx1.example.com
      - 20 mx2.example.com
    exclusive: true
  delegate_to: localhost

- name: Add an SPF record using a domain key
  mydnshost.dns.record:
    domain: example.com
    domain_key: "{{ vault_example_com_key }}"
    name: example.com
    type: TXT
    value: v=spf1 mx -all
  delegate_to: localhost

- name: Remove all AAAA records for a name
  mydnshost.dns.record:
    name: old.example.com
    type: AAAA
    state: absent
  delegate_to: localhost
'''

RETURN = r'''
domain:
  description: The domain the record belongs to.
  returned: always
  type: str
  sample: example.com
name:
  description: The record name, relative to the domain (empty for the apex).
  returned: always
  type: str
  sample: www
records:
  description:
    - The records for this name and type after the change.
    - In check mode, or when nothing changed, this is the expected result; new records have no C(id).
  returned: always
  type: list
  elements: dict
  sample:
    - id: 1234
      name: www
      type: A
      content: 192.0.2.10
      ttl: 300
      priority: null
serial:
  description: The zone serial after the change.
  returned: when changes were applied
  type: int
  sample: 2026102701
'''

import traceback

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.mydnshost.dns.plugins.module_utils.mydnshost import (
    MyDnsApiException,
    RECORD_TYPES,
    auth_argument_spec,
    build_client,
    build_request,
    find_domain,
    format_records,
    has_changes,
    normalise_domain,
    plan_changes,
    predict_records,
    relative_name,
)


def main():
    argument_spec = auth_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        domain=dict(type='str'),
        type=dict(type='str', required=True, choices=RECORD_TYPES),
        values=dict(type='list', elements='str', aliases=['value', 'content']),
        ttl=dict(type='int'),
        exclusive=dict(type='bool', default=False),
        state=dict(type='str', choices=['present', 'absent'], default='present'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        required_if=[('state', 'present', ['values'])],
        supports_check_mode=True,
    )

    params = module.params
    rtype = params['type']
    domain = normalise_domain(params['domain']) if params['domain'] else None

    api = build_client(module, domain)

    try:
        if domain is None:
            domain = find_domain(api.get_domains().keys(), params['name'])
            if domain is None:
                module.fail_json(msg='Unable to find a domain on this account containing %s' % params['name'])

        try:
            name = relative_name(params['name'], domain)
            existing = api.get_domain_records_by_name(domain, name or '@', rtype) or []
            plan = plan_changes(rtype, existing, params['values'], ttl=params['ttl'], state=params['state'],
                                exclusive=params['exclusive'])
        except ValueError as e:
            module.fail_json(msg=str(e))

        records = predict_records(name, rtype, existing, plan, params['ttl'])
        result = dict(changed=has_changes(plan), domain=domain, name=name, records=records)

        if module._diff:
            result['diff'] = dict(
                before=format_records(domain, existing),
                after=format_records(domain, records),
                before_header='%s %s' % (name or '@', rtype),
                after_header='%s %s' % (name or '@', rtype),
            )

        if result['changed'] and not module.check_mode:
            response = api.set_domain_records(domain, {'records': build_request(name, rtype, plan, params['ttl'])})
            result['serial'] = response.get('serial')
            result['records'] = api.get_domain_records_by_name(domain, name or '@', rtype) or []
    except MyDnsApiException as e:
        module.fail_json(msg='MyDNSHost API error: %s' % e, error_data=getattr(e, 'error_data', None))
    except Exception as e:
        module.fail_json(msg='Error talking to the MyDNSHost API: %s' % e, exception=traceback.format_exc())

    module.exit_json(**result)


if __name__ == '__main__':
    main()
