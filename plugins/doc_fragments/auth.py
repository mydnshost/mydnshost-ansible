# -*- coding: utf-8 -*-
# MIT License (see LICENCE)

from __future__ import absolute_import, division, print_function
__metaclass__ = type


class ModuleDocFragment(object):

    DOCUMENTATION = r'''
options:
  api_url:
    description:
      - Base URL of the MyDNSHost API.
      - If not set, the E(MYDNSHOST_API_URL) environment variable is used, falling back
        to C(https://api.mydnshost.co.uk/).
    type: str
  auth_user:
    description:
      - Username (email address) to authenticate as, used with O(auth_key).
      - If not set, the E(MYDNSHOST_AUTH_USER) environment variable is used.
    type: str
  auth_key:
    description:
      - User API key to authenticate with, used with O(auth_user).
      - If not set, the E(MYDNSHOST_AUTH_KEY) environment variable is used.
      - Mutually exclusive with O(domain_key).
    type: str
  domain_key:
    description:
      - Domain key to authenticate with. Requires O(domain) to be set.
      - If not set, the E(MYDNSHOST_DOMAIN_KEY) environment variable is used.
      - Mutually exclusive with O(auth_key).
    type: str
  timeout:
    description:
      - Timeout in seconds for API requests.
    type: int
    default: 30
requirements:
  - mydnshost (U(https://github.com/mydnshost/mydnshost-python-api))
'''
