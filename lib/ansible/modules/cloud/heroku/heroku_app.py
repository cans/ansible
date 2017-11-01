#!/usr/bin/python
# -*- coding: utf-8; -*-
# Copyright © 2017, Nicolas CANIART
# GNU General Public License v2.0 (see COPYING or https://www.gnu.org/licenses/gpl-2.0.txt)
from __future__ import absolute_import, division, print_function

ANSIBLE_METADATA = {'metadata_version': '1.1',
                    'supported_by': 'community',
                    'status': ['preview'],
                    }

DOCUMENTATION = '''
---
module: heroku_app
short_description: Manages Heroku applications
description:
    - Let you create, delete, scale, ... Heroku applications
version_added: 2.8
author: Nicolas CANIART (@cans)
options:
  api_key:
    description: API key to authenticate with Heroku
    required: True

  app:
    description: Name of your Heroku application which state change
    required: True

  count:
    description:
      - The number of dynos to allocate for the application
      - Use of this option is mutually exclusive with the I(formation) option.
      - When used, this option requires that you also define the I(size) option.
      - Ignored when C(state=absent), C(state=present) or C(state=stopped).
    required: False
  formation:
    default: '{}'
    description:
      - Describes the set of dynos to allocate to your application.
      - Use of this option is mutually exclusive with the I(count) and I(size) option.
      - Ignored when C(state=absent), C(state=present) or C(state=stopped).
      - Note that compared to the I(size) argument you need to strip dashes ('-') from dyno type names.
    required: False

  region:
    default: 'us'
    description:
      - The datacenter in which host the application.
    choices:
      - eu
      - frankfurt
      - oregon
      - tokyo
      - us
      - virginia
  settings:
    default: '{}'
    description:
      - Dictionary of settings to be passed to your application
    required: False
  size:
    description:
      - The type of _dyno_ to use
      - Use of this option is mutually exclusive witht the I(formation) option.
      - When used, this option requires that you also define the I(count) option.
      - Ignored when C(state=absent), C(state=present) or C(state=stopped).
    required: False
  stack:
    default: 'heroku-16'
    description:
      - The Heroku stack to use U(https://devcenter.heroku.com/articles/stack)
  state:
    default: present
    description:
      - The state your application should end-up in.
      - C(absent) means the application should not exists, if it does it is deleted.
      - C(present) means the application should exists, if it does not it is created but has a mere empty shell.
      - C(restarted) means the application should be restarted, if it is running or started if it is C(stopped).
      - C(started) means the application should exist and be running. If it is C(stopped) it is started, if it does not exists the module will fail.
      - C(stopped) means the application should exist and no be running. If it is running it is stopped, if it does not exists the module will fail.
    choices:
      - absent
      - present
      - restarted
      - started
      - stopped
    required: True
  uppercase:
    default: 'False'
    description: Whether to uppercase setting names before sending them to your application
    required: False

requirements:
    - heroku3 Python module
'''

EXAMPLES = '''
- name: Create basic app (with homogeneous dyno types)
  heroku_app:
    apikey: "<your-heroku-api-key>"
    app: "my-app"
    size: "standard-1x"
    count: 3

- name: Create a new application if it does not exist yet
  heroku_app:
    apikey: "<your-heroku-api-key>"
    app: "my-app"
    state: "present"
    region: "us"
    settings:
      variable: "value"
      path: "/to/some/place"
    uppercase: true
    formation:
      standard1x: 3
      standard2x: 1

- name: Delete an application
  heroku_app:
    apikey: "<your-heroku-api-key>"
    app: "my-app"
    state: "absent"
'''

RETURN = '''
heroku_app:
  description: "a description of the application's state"
  returned: always
  sample: 'heroku_app: {"name": "some-app", "id": "<uuid>"}'
  type: complex
  contains:
    name:
      description: "name of the application"
      type: string
      returned: always
    id:
      description: "application unique id"
      type: string
      sample: "12345678-90ab-cdef-1234-567890abcdef"
      returned: "when state != absent"
    formation:
      description: "process formation of the application"
      type: "list of dictionaries"
      returned: "when state in ['started', 'restarted']"
      sample: '{"standard1x": 1, "standard2x": 2}'
    settings:
      description:
        - the application user defined settings.
        - be mindful of sensitive data potentially exposed here !
      type: dictionary
      returned: "when state in ['started', 'restarted']"
      sample: values are user defined
state:
  description: "the current state of the application"
  returned: always
  sample: "started"
  type: string
'''

import copy
from itertools import izip_longest
import sys

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.six import iteritems
# WANT_JSON
try:
    from requests.exceptions import HTTPError
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
try:
    import heroku3
    from heroku3.models import BaseResource
    HAS_HEROKU3 = True
except ImportError:
    HAS_HEROKU3 = False


__all__ = ['main']
__metaclass__ = type

_STATE_ABSENT = 'absent'
_STATE_PRESENT = 'present'
_STATE_RESTARTED = 'restarted'
_STATE_STARTED = 'started'
_STATE_STOPPED = 'stopped'
_STATES = (_STATE_ABSENT,
           _STATE_PRESENT,
           _STATE_RESTARTED,
           _STATE_STARTED,
           _STATE_STOPPED,
           )
_DYNO_SIZES = ('free',
               'hobby',
               'standard-1x',
               'standard-2x',
               'performance-m',
               'performance-l',
               )
# _DYNO_WORKLOADS = ('web',
#                    'worker',
#                    )
_HEROKU_REGION_EUROPE = 'eu'
_HEROKU_REGION_FRANKFURT = 'frankfurt'
_HEROKU_REGION_OREGON = 'oregon'
_HEROKU_REGION_TOKYO = 'tokyo'
_HEROKU_REGION_USA = 'us'
_HEROKU_REGION_VIRGINIA = 'virginia'
_HEROKU_REGIONS = (_HEROKU_REGION_EUROPE,
                   _HEROKU_REGION_FRANKFURT,
                   _HEROKU_REGION_OREGON,
                   _HEROKU_REGION_TOKYO,
                   _HEROKU_REGION_USA,
                   _HEROKU_REGION_VIRGINIA,
                   )
_HEROKU_STACK_CEDAR14 = 'cedar-14'
_HEROKU_STACK_HEROKU16 = 'heroku-16'
_HEROKU_STACKS = (_HEROKU_STACK_CEDAR14,
                  _HEROKU_STACK_HEROKU16,
                  )

_ARGS_SPEC = {'api_key': {'required': True,
                          'no_log': True,
                          'type': 'str',
                          },
              'app': {'required': True,
                      # 'type': 'list',
                      'type': 'str',
                      },
              'count': {'required': False,
                        'type': 'int',
                        'default': None,
                        },
              'formation': {'required': False,
                            'type': 'dict',
                            'default': {},
                            },
              'region': {'default': _HEROKU_REGION_USA,
                         'choices': _HEROKU_REGIONS,
                         },
              'settings': {'required': False,
                           'type': 'dict',
                           'default': {},
                           },
              'size': {'default': None,
                       'choices': _DYNO_SIZES,
                       },
              'stack': {'default': _HEROKU_STACK_HEROKU16,
                        'choices': _HEROKU_STACKS,
                        'type': 'str',
                        },
              'state': {'default': _STATE_PRESENT,
                        'choices': _STATES,
                        'type': 'str',
                        },
              'uppercase': {'required': False,
                            'type': 'bool',
                            'default': False,
                            },
              # Dyno types comes from the Procfile, no possibility to
              # change it.
              # 'workload': {'required': False,
              #              'choices': _DYNO_WORKLOADS,
              #              }
              }


_UNABLE_TO_RETRIEVE_APP_DATA = """
Unabled to retrieve applications data from Heroku
(check your credentials or Heroku's status)."""


def _absent(module, client, hk_app, **kwargs):
    if hk_app is None:
        module.exit_json(changed=False)

    try:
        hk_app.delete()
    except HTTPError as e:
        module.fail_json(msg="Fail to delete App `{}'.".format(hk_app.name))

    else:
        module.exit_json(changed=True, msg="App `{}' deleted.".format(hk_app.name))


def _check_app(module, client, app):
    try:
        apps = client.apps()
    except HTTPError:
        module.fail_json(msg=_UNABLE_TO_RETRIEVE_APP_DATA)

    if app not in apps:
        return None
    else:
        return apps.get(app)


def _check_prerequisites(module, count=None, formation=None, state=None, size=None, **kwargs):
    if state in (_STATE_ABSENT, _STATE_PRESENT, _STATE_STOPPED):
        return  # We don't care about 'count', 'formation' and 'size'

    if((not formation and (size is None or count is None)) or
       (formation and size is not None and count is not None)):
        module.fail_json(msg="You must specify either the 'formation' or both "
                         "the 'count' and 'size' options.")

    total_dynos = 0
    safe_dyno_types = {dyno_type.replace('-', ''): dyno_type for dyno_type in _DYNO_SIZES}
    for dyno_type, count in copy.copy(formation).items():
        actual_dyno_type = safe_dyno_types.get(dyno_type)
        if actual_dyno_type is None or count < 0:
            module.fail_json(msg=("Invalid 'formation' value: '{dyno_type}: {count}'"
                                  .format(dyno_type=dyno_type, count=count)))
        else:
            formation[actual_dyno_type] = count
            del formation[dyno_type]
        total_dynos += count

    if not formation:
        formation.update({size: count, })
        total_dynos += count

    if state != 'present' and total_dynos <= 0:
        module.fail_json(msg=("You allocated no dynos to your application."
                              " It cannot be running without dynos."
                              " Check you formation or size and count options."
                              )
                         )
    # Parameters 'count', 'region', 'size', 'stack', 'state' already checked by the
    # module.
    # Cannot check 'settings' it is user data.


def _configure(module, client, hk_app, settings=None, uppercase=True, **kwargs):
    """Applies settings to the given Heroku application.

    Args:
        module (AnsibleModule): an Ansible module
        client (HerokuClient): an client for Heroku's API
        hk_app (HerokuApp): an Heroku application
        settings (dict[str, Any]): the settings to apply to ``hk_app``
        uppercase (bool): whether to turn settings dict keys to upper
            case before applying settings to ``hk_app``.
    """
    assert hk_app is not None

    results = []
    unknown = object()
    success = True
    try:
        current_settings = hk_app.config()
        current_settings = current_settings.data
    except Exception as e:  # TODO: Better handle error
        success = False
        message = ("Failed to update `{}' application's configuration: {}"
                   .format(kwargs['app'], e)
                   )
        module.fail_json(msg=message)

    changed = False
    actual_settings = {}
    for variable, new_value in iteritems(settings):
        if uppercase is True:
            actual_variable = variable.upper()
        else:
            actual_variable = variable

        actual_settings[actual_variable] = str(new_value)
        old_value = current_settings.get(variable, unknown)
        changed = old_value is unknown or str(new_value) != old_value

    hk_app.update_config(actual_settings)
    return changed, hk_app


def _create(module, client, app=None, region=None, stack=None, **kwargs):
    """Creates a new Heroku application

    Args:
        module (AnsibleModule): the Ansible module instance
        client (heroku3.api.Heroku): the Heroku API client
        app (str): the name of the app to create
        stack (str): the name of the Heroku stack to use
        region (str): the Heroku region in which host the the app

    Returns:
        Application:
    """
    if module.check_mode is False:  # Change state only if not in check mode.
        return None
    try:
        hk_app = client.create_app(name=app, stack_id_or_name=stack, region_id_or_name=region)
        return hk_app

    except HTTPError as e:
        module.fail_json(msg="Could not create App {app}: {error}"
                         .format(app=app, error=e))


def _convert_facts(fact, exclude=None):
    result = dict()
    exclude = exclude or ['app', 'info', 'order_by', ]
    attributes = dir(fact)
    for attr in attributes:
        if attr.startswith('_') or attr in exclude:
            continue

        value = getattr(fact, attr)
        if callable(value):
            continue
        elif isinstance(value, BaseResource):
            result[attr] = _convert_facts(value, exclude=exclude)
        else:
            result[attr] = value
    return result


def _get_region_facts(region):
    return dict(description=region.description, id=region.id, name=region.name)


def _get_user_facts(user):
    """Given a user returns a dict without circular object references

    Heroku's API can link user objects in several places, and sometimes that
    user object holds back reference to e.g. an application object. Which makes
    it hard to serialize the object. This function turn the given user into a
    dictionary without such circular references.

    Args:
        user (heroku3.models.User): the object to screen

    Returns:
        dict[str, Any]

    """
    return dict(id=user.id, email=user.email, )


def _get_app_facts(hk_app, **kwargs):
    if hk_app is None:  # Probably in check mode.
        result = dict(name=kwargs['app'])

    else:
        collaborators = [_get_user_facts(collab.user)
                         for collab in hk_app.info.collaborators()]

        result = dict(collaborators=collaborators,
                      config=hk_app.info.config().to_dict(),
                      git_url=hk_app.info.git_url,
                      id=hk_app.id,
                      name=hk_app.name,
                      owner=_get_user_facts(hk_app.info.owner),
                      region=hk_app.info.region.__dict__,
                      web_url=hk_app.info.web_url,
                      )

    return dict(heroku_app=result)


def _passive(verb):
    if verb[-1] in ['p']:
        return "{}{}ed".format(verb, verb[-1])
    elif verb[-1] in ['t']:
        return '{}ed'.format(verb)
    return '{}d'.format(verb)


def _present(module, client, hk_app, **kwargs):
    changed = False
    if hk_app is None:
        hk_app = _create(module, client, **kwargs)
        changed = True

    return changed, _get_app_facts(hk_app, **kwargs)


def _restart(module, client, hk_app, **kwargs):
    hk_app.restart()
    return True, hk_app


def _restarted(module, client, hk_app, **kwargs):
    if hk_app is None:
        return _started(module, client, hk_app, **kwargs)

    # Better configure the application before re-starting it
    uppercase = kwargs.get('uppercase',
                           _ARGS_SPEC['uppercase']['default'],
                           )
    changed, hk_app = _configure(module,
                                 client,
                                 hk_app,
                                 settings=kwargs['settings'],
                                 uppercase=uppercase,
                                 )

    return _restart(module, client, hk_app)


def _scale(module, client, hk_app, formation=None, workload=None, **kwargs):
    _scale_app(module, hk_app, formation=formation)


def _scale_app(module, hk_app, quantity=None, size=None, **kwargs):
    """Given an app, up- or down-scales it.

    If original quantity was 0 and the new one is positive, then this
    starts the application.

    Conversely, if the original quantity is positive and the new one is
    zero, then this stops the application.

    Args:
        module (AnsibleModule): the Ansible module instance
        hk_app (heroku3.models.App): the application to scale
        quantity (int): the number of dynos required to be present.
        size (int): the type of Dyno to use.

    Returns:
        None: has a side effect on the module
    """
    assert hk_app is not None

    action = None
    try:
        formation = hk_app.process_formation()  # Get the "formation"
        # Encapsulation violation: implement __len__ on KeyedListResources
        formation_count = len(formation)
        if formation_count > 1:
            module.fail_json(msg="Unhandled use case: application with several process formations")

        if formation_count == 0:
            module.fail_json(msg="App `{app}' has process formation: have you deployed some code on it ?")

        formation = formation[0]

        if formation.quantity < quantity:
            if formation.quantity == 0:
                action = 'start'
            else:
                action = 'up-scale'
        elif formation.quantity > quantity:
            if quantity == 0:
                action = 'stop'
            else:
                action = 'down-scale'

        if formation and (formation.quantity != quantity or size != formation.size):
            formation.update(size=size, quantity=quantity)

    except HTTPError as e:
        module.fail_json(msg="Could not {action} app `{app}': {error}"
                         .format(action=action, app=hk_app.name, error=e))
    else:
        if action is not None:
            msg = ('App {app} successfully {action}.'
                   .format(app=hk_app.name, action=_passive(action))
                   )
        else:
            msg = "App `{app}' left unchanged.".format(app=hk_app.name)
        module.exit_json(changed=action is not None, msg=msg)


def _started(module, client, hk_app, size=None, count=None, formation=None, **kwargs):
    changed, hk_app = _present(module, client, hk_app, **kwargs)
    if changed is True and module.check_mode is True:  # App doesn't exist yet
        return changed, hk_app

    # Better configure the application before starting it
    changed, hk_app = _configure(module,
                                 client,
                                 hk_app,
                                 settings=kwargs['settings'],
                                 uppercase=kwargs.get('uppercase',
                                                      _ARGS_SPEC['uppercase']['default'],
                                                      ),
                                 )

    return _scale(module, client, hk_app, size=size, count=count, **kwargs)


def _stopped(module, client, hk_app, **kwargs):
    print(kwargs, file=sys.stderr)
    if hk_app is None:
        return False, hk_app

    formation_override = dict(izip_longest(_DYNO_SIZES, [0], fillvalue=0))
    changed, hk_app = _scale(module, client, hk_app, formation=formation_override)

    return changed, hk_app


def main():
    global _ARGS_SPEC
    _STATE_HANDLERS = {'absent': _absent,
                       'present': _present,
                       'restarted': _restarted,
                       'started': _started,
                       'stopped': _stopped,
                       }

    module = AnsibleModule(argument_spec=_ARGS_SPEC,
                           supports_check_mode=True,
                           )
    if not HAS_HEROKU3:
        module.fail_json(msg="Heroku3 is required for this module. Please install heroku3 and try again.")
    if not HAS_REQUESTS:
        module.fail_json(msg="Requests is required for this module. Please install requests and try again.")

    params = module.params
    _check_prerequisites(module, **module.params)
    client = heroku3.from_key(params['api_key'])
    del params['api_key']  # So it does not appear in logs.

    command = _STATE_HANDLERS[params['state']]
    hk_app = _check_app(module, client, params['app'])
    changed, result = command(module,
                              client,
                              hk_app,
                              **{k: v
                                 for k, v in iteritems(module.params)
                                 if k not in ['state', ]
                                 }
                              )

    module.exit_json(changed=changed, state=params['state'], **result)


if __name__ == '__main__':
    main()
