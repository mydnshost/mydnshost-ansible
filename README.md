# mydnshost.dns

Ansible collection for managing DNS records on [MyDNSHost](https://www.mydnshost.co.uk/).

It contains:

- `mydnshost.dns.record`: a module that manages the records for one name and type.
- `mydnshost.dns.records`: a role that applies a list of records using that module.

## Installation

Install the collection:

```
ansible-galaxy collection install git+https://github.com/mydnshost/mydnshost-ansible.git
```

The module needs the [mydnshost python API](https://github.com/mydnshost/mydnshost-python-api) installed in the
Python environment the module runs in. This is normally the controller, as tasks are delegated to `localhost`:

```
pip install git+https://github.com/mydnshost/mydnshost-python-api.git

# or, if ansible is installed with pipx:
pipx inject ansible-core git+https://github.com/mydnshost/mydnshost-python-api.git
```

## Authentication

Either a user API key or a domain key can be used:

| Option        | Environment variable   | Notes                                    |
|---------------|------------------------|------------------------------------------|
| `api_url`     | `MYDNSHOST_API_URL`    | Defaults to `https://api.mydnshost.co.uk/` |
| `auth_user`   | `MYDNSHOST_AUTH_USER`  | Used with `auth_key`                     |
| `auth_key`    | `MYDNSHOST_AUTH_KEY`   | User API key                             |
| `domain_key`  | `MYDNSHOST_DOMAIN_KEY` | Domain key; `domain` must also be set    |

These can be passed to each task, but it's easier to set them once for a play (or block) with the
`mydnshost.dns.mydnshost` module defaults group:

```yaml
- hosts: localhost
  module_defaults:
    group/mydnshost.dns.mydnshost:
      auth_user: admin@example.com
      auth_key: "{{ vault_mydnshost_key }}"
  tasks:
    - mydnshost.dns.record:
        name: www.example.com
        type: A
        value: 192.0.2.10
```

Anything not set falls back to the environment variables, which match those used by the `mydnshost` CLI.

## Using the module

Each task manages the records for one name and type. Values are added if missing; with `exclusive: true` any other
values for that name and type are removed, so the record set matches exactly.

```yaml
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

- name: Set mail servers for the apex
  mydnshost.dns.record:
    domain: example.com
    name: "@"
    type: MX
    values:
      - 10 mx1.example.com
      - 20 mx2.example.com
    exclusive: true
  delegate_to: localhost

- name: Remove all AAAA records for a name
  mydnshost.dns.record:
    name: old.example.com
    type: AAAA
    state: absent
  delegate_to: localhost
```

- `name` can be fully qualified or relative to `domain`. If `domain` isn't given, the most specific domain on the
  account containing `name` is used.
- MX, SRV, SVCB and HTTPS values start with the priority, zone-file style (`10 5 5060 sip.example.com`).
- TXT values are given without surrounding quotes.
- `ttl` is applied to new records and to existing records matching `values`. If unset, new records use the domain
  default and existing TTLs are left alone.
- All changes for a task are sent in one request, which the API applies atomically.
- Check mode and `--diff` are supported.

See `ansible-doc mydnshost.dns.record` for full details.

## Using the role

```yaml
- hosts: webservers
  roles:
    - role: mydnshost.dns.records
      vars:
        mydnshost_auth_user: admin@example.com
        mydnshost_auth_key: "{{ vault_mydnshost_key }}"
        mydnshost_records:
          - name: "{{ inventory_hostname }}"
            type: A
            values: ["{{ ansible_default_ipv4.address }}"]
            exclusive: true
          - name: "{{ inventory_hostname }}"
            type: AAAA
            values: ["{{ ansible_default_ipv6.address }}"]
            exclusive: true
```

Each entry in `mydnshost_records` takes the module's `name`, `type`, `values`, `ttl`, `exclusive`, `state` and
`domain` options. Tasks are delegated to `localhost`. Connection settings come from the `mydnshost_api_url`,
`mydnshost_auth_user`, `mydnshost_auth_key` and `mydnshost_domain_key` vars if set, then from module defaults,
then from the environment.

If every host in the play applies the *same* records, set `mydnshost_records_run_once: true`. Otherwise the hosts
apply them in parallel, and identical adds can race and create duplicate records.

## Limitations

- Changing a name between CNAME and other record types needs the old records removed in an earlier task, as the API
  won't allow a CNAME alongside other records.
- The `disabled` and `comment` record fields aren't managed.

## Development

Unit and sanity tests use `ansible-test`, which needs the collection checked out at
`ansible_collections/mydnshost/dns`:

```
ansible-test sanity --venv
ansible-test units --venv
```

`tests/integration/records.yml` exercises the module and role against a real API. It adds and removes records
under `test-ansible.<test_domain>`, so point it at a throwaway domain:

```
ansible-playbook tests/integration/records.yml -e test_domain=test.example \
  -e mydnshost_api_url=https://api.dev.mydnshost.co.uk/ \
  -e mydnshost_auth_user=... -e mydnshost_auth_key=...
```
