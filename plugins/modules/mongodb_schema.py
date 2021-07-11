#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2021, Rhys Campbell (@rhysmeister) <rhyscampbell@bluewin.ch>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function
__metaclass__ = type


DOCUMENTATION = r'''
---
module: mongodb_schema

short_description: Manages MongoDB Document Schema Validators.

description:
- Manages MongoDB Document Schema Validators.
- CReate, update and remove Validatorson a collection.

author: Rhys Campbell (@rhysmeister)
version_added: "1.0.0"

extends_documentation_fragment:
  - community.mongodb.login_options
  - community.mongodb.ssl_options

options:
  db:
    description:
      - The database to work with
    required: yes
    type: str
  collection:
    description:
      - The collection to work with.
    required: yes
    type: str
  bsonType:
    description:
      - Expected document type.
    type: str
    default: "object"
  required:
    description:
      - List of fields that are required.
    type: list
    elements: str
  properties:
    description:
      - Individual property specification.
    type: dict
  action:
    description:
      - The validation action to perform.
    type: str
    choices:
      - "error"
      - "warn"
    default: "error"
  level:
    description:
      - The validation level.
    type: str
    choices:
      - "strict"
      - "moderate"
    default: "strict"
  state:
    description:
      - The state of the validator.
    type: str
    choices:
      - "present"
      - "absent"
    default: "present"

notes:
    - Requires the pymongo Python package on the remote host, version 2.4.2+.

requirements:
  - pymongo
'''

EXAMPLES = r'''
- name: Require that an email address field is in every document
  community.mongodb.mongodb_schema:
    db: "rhys"
    collection: "contacts"
    required:
      - "email"

- name: Remove a validator on a collection
  community.mongodb.mongodb_schema:
    db: "rhys"
    collection: "contacts"
    state: "absent"
'''

RETURN = r'''

'''

from uuid import UUID

import ssl as ssl_lib
from distutils.version import LooseVersion

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils._text import to_native
from ansible.module_utils.six import iteritems
from ansible_collections.community.mongodb.plugins.module_utils.mongodb_common import (
    check_compatibility,
    missing_required_lib,
    mongodb_common_argument_spec,
    ssl_connection_options
)
from ansible_collections.community.mongodb.plugins.module_utils.mongodb_common import PyMongoVersion, PYMONGO_IMP_ERR, pymongo_found, MongoClient


has_ordereddict = False
try:
    from collections import OrderedDict
    has_ordereddict = True
except ImportError as excep:
    try:
        from ordereddict import OrderedDict
        has_ordereddict = True
    except ImportError as excep:
        pass


def get_validator(client, db, collection):
    validator = None
    cmd_doc = OrderedDict([
        ('listCollections', 1),
        ('filter', { "name": collection })
    ])
    doc = None
    results = client[db].command(cmd_doc)["cursor"]["firstBatch"]
    if len(results) > 0:
        doc = results[0]
    if doc is not None and 'options' in doc and 'validator' in doc['options']:
        validator = doc['options']['validator']["$jsonSchema"]
    return validator


def validator_is_different(client, db, collection, bsonType, required, properties, action, level):
    is_different = False
    validator = get_validator(client, db, collection)
    if validator is not None:
        if bsonType != validator['bsonType']:
            is_different = True
        if not is_different and sorted(required) != sorted(validator['required']):
            is_different = True
        if not is_different and action != validator['validationAction']:
            is_different = True
        if not is_different and level != validator['validationLevel']:
            is_different = True
        if not is_different:
            dict1 = json.dumps(properties, sort_keys=True)
            dict2 = json.dumps(validator['properties'], sort_keys=True)
            if dict1 != dict2:
                is_different = True
    else:
        is_different = True


def add_validator(client, db, collection, bsonType, required, properties, action, level):
    cmd_doc = OrderedDict([
        ('collMod', collection),
        ('validator', { '$jsonSchema': {
                            "bsonType": bsonType,
                            "required": required,
                            "properties": properties
                            }
                       }),
        ('validationAction', action),
        ('validationLevel', level)
    ])
    client[db].create_collection(collection)
    client[db].command(cmd_doc)


def remove_validator(client, db, collection):
    cmd_doc = OrderedDict([
        ('collMod', collection),
        ('validator', {}),
        ('validationLevel', "off")
    ])
    client[db].command(cmd_doc)


# ================
# Module execution
#

def main():
    argument_spec = mongodb_common_argument_spec()
    argument_spec.update(
        db=dict(type='str', required=True),
        collection=dict(type='str', required=True),
        bsonType=dict(type='str', default="object"),
        required=dict(type='list', elements='str'),
        properties=dict(type='dict', default={}),
        action=dict(type='str', choices=['error', 'warn'], default="error"),
        level=dict(type='str', choices=['strict', 'moderate'], default="strict"),
        state=dict(type='str', choices=['present', 'absent'], default='present')
    )
    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
        required_together=[['login_user', 'login_password']],
        required_if=[("state", "present", ("db", "collection"))]
    )

    if not has_ordereddict:
        module.fail_json(msg='Cannot import OrderedDict class. You can probably install with: pip install ordereddict')

    if not pymongo_found:
        module.fail_json(msg=missing_required_lib('pymongo'),
                         exception=PYMONGO_IMP_ERR)

    login_user = module.params['login_user']
    login_password = module.params['login_password']
    login_database = module.params['login_database']
    login_host = module.params['login_host']
    login_port = module.params['login_port']
    ssl = module.params['ssl']
    db = module.params['db']
    collection = module.params['collection']
    bsonType = module.params['bsonType']
    required = module.params['required']
    properties = module.params['properties']
    action = module.params['action']
    level = module.params['level']
    state = module.params['state']

    connection_params = {
        'host': login_host,
        'port': login_port,
    }

    if ssl:
        connection_params = ssl_connection_options(connection_params, module)

    client = MongoClient(**connection_params)

    if login_user:
        try:
            client.admin.authenticate(login_user, login_password, source=login_database)
        except Exception as e:
            module.fail_json(msg='Unable to authenticate: %s' % to_native(e))

    # Get server version:
    try:
        srv_version = LooseVersion(client.server_info()['version'])
    except Exception as e:
        module.fail_json(msg='Unable to get MongoDB server version: %s' % to_native(e))

    # Get driver version::
    driver_version = LooseVersion(PyMongoVersion)

    # Check driver and server version compatibility:
    check_compatibility(module, srv_version, driver_version)

    result = dict(
        changed=False,
    )

    validator = get_validator(client, db, collection)
    if state == "present":
        if validator is not None:
            diff = validator_is_different(client, db, collection, bsonType,
                                          required, properties, action, level)
            if diff:
                if not module.check_mode:
                    add_validator(client,
                                  db,
                                  collection,
                                  bsonType,
                                  required,
                                  properties,
                                  action,
                                  level)
                result['changed'] = True
                result['msg'] = "The validator was updated on the given collection"
            else:
                result['changed'] = False
                result['msg'] = "The validator exists as configured on the given collection"
        else:
            if not module.check_mode:
                add_validator(client,
                              db,
                              collection,
                              bsonType,
                              required,
                              properties,
                              action,
                              level)
            result['changed'] = True
            result['msg'] = "The validator has been added to the given collection"
    elif state == "absent":
        if validator is None:
            result['changed'] = False
            result['msg'] = "A validator does not exist on the given collection."
        else:
            if not module.check_mode:
                remove_validator(client, db, collection)
            result['changed'] = True
            result['msg'] = "The validator has been removed from the given collection"


    module.exit_json(**result)


if __name__ == '__main__':
    main()
