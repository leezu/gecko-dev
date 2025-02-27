# -*- coding: utf-8 -*-

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from __future__ import absolute_import, print_function, unicode_literals

import json
import os
import inspect
import re
import yaml
from slugid import nice as slugid
from mozbuild.util import memoize
from types import FunctionType
from collections import namedtuple
from taskgraph import create
from taskgraph.util import taskcluster
from taskgraph.util.docker import docker_image
from taskgraph.parameters import Parameters


GECKO = os.path.realpath(os.path.join(__file__, '..', '..', '..'))

actions = []
callbacks = {}

Action = namedtuple('Action', [
    'name', 'title', 'description', 'order', 'context', 'schema', 'task_template_builder',
])


def is_json(data):
    """ Return ``True``, if ``data`` is a JSON serializable data structure. """
    try:
        json.dumps(data)
    except ValueError:
        return False
    return True


def register_task_action(name, title, description, order, context, schema=None):
    """
    Register an action task that can be triggered from supporting
    user interfaces, such as Treeherder.

    Most actions will create intermediate action tasks that call back into
    in-tree python code. To write such an action please use
    :func:`register_callback_action`.

    This function is to be used a decorator for a function that returns a task
    template, see :doc:`specification <action-spec>` for details on the
    templating features. The decorated function will be given decision task
    parameters, which can be embedded in the task template that is returned.

    Parameters
    ----------
    name : str
        An identifier for this action, used by UIs to find the action.
    title : str
        A human readable title for the action to be used as label on a button
        or text on a link for triggering the action.
    description : str
        A human readable description of the action in **markdown**.
        This will be display as tooltip and in dialog window when the action
        is triggered. This is a good place to describe how to use the action.
    order : int
        Order of the action in menus, this is relative to the ``order`` of
        other actions declared.
    context : list of dict
        List of tag-sets specifying which tasks the action is can take as input.
        If no tag-sets is specified as input the action is related to the
        entire task-group, and won't be triggered with a given task.

        Otherwise, if ``context = [{'k': 'b', 'p': 'l'}, {'k': 't'}]`` will only
        be displayed in the context menu for tasks that has
        ``task.tags.k == 'b' && task.tags.p = 'l'`` or ``task.tags.k = 't'``.
        Esentially, this allows filtering on ``task.tags``.
    schema : dict
        JSON schema specifying input accepted by the action.
        This is optional and can be left ``null`` if no input is taken.

    Returns
    -------
    function
        To be used as decorator for the function that builds the task template.
        The decorated function will be given decision parameters and may return
        ``None`` instead of a task template, if the action is disabled.
    """
    assert isinstance(name, basestring), 'name must be a string'
    assert isinstance(title, basestring), 'title must be a string'
    assert isinstance(description, basestring), 'description must be a string'
    assert isinstance(order, int), 'order must be an integer'
    assert is_json(schema), 'schema must be a JSON compatible  object'
    mem = {"registered": False}  # workaround nonlocal missing in 2.x

    def register_task_template_builder(task_template_builder):
        assert not mem['registered'], 'register_task_action must be used as decorator'
        actions.append(Action(
            name.strip(), title.strip(), description.strip(), order, context,
            schema, task_template_builder,
        ))
        mem['registered'] = True
    return register_task_template_builder


def register_callback_action(name, title, symbol, description, order=10000,
                             context=[], available=lambda parameters: True, schema=None):
    """
    Register an action callback that can be triggered from supporting
    user interfaces, such as Treeherder.

    This function is to be used as a decorator for a callback that takes
    parameters as follows:

    ``parameters``:
        Decision task parameters, see ``taskgraph.parameters.Parameters``.
    ``input``:
        Input matching specified JSON schema, ``None`` if no ``schema``
        parameter is given to ``register_callback_action``.
    ``task_group_id``:
        The id of the task-group this was triggered for.
    ``task_id`` and `task``:
        task identifier and task definition for task the action was triggered
        for, ``None`` if no ``context`` parameters was given to
        ``register_callback_action``.

    Parameters
    ----------
    name : str
        An identifier for this action, used by UIs to find the action.
    title : str
        A human readable title for the action to be used as label on a button
        or text on a link for triggering the action.
    symbol : str
        Treeherder symbol for the action callback, this is the symbol that the
        task calling your callback will be displayed as. This is usually 1-3
        letters abbreviating the action title.
    description : str
        A human readable description of the action in **markdown**.
        This will be display as tooltip and in dialog window when the action
        is triggered. This is a good place to describe how to use the action.
    order : int
        Order of the action in menus, this is relative to the ``order`` of
        other actions declared.
    context : list of dict
        List of tag-sets specifying which tasks the action is can take as input.
        If no tag-sets is specified as input the action is related to the
        entire task-group, and won't be triggered with a given task.

        Otherwise, if ``context = [{'k': 'b', 'p': 'l'}, {'k': 't'}]`` will only
        be displayed in the context menu for tasks that has
        ``task.tags.k == 'b' && task.tags.p = 'l'`` or ``task.tags.k = 't'``.
        Esentially, this allows filtering on ``task.tags``.
    available : function
        An optional function that given decision parameters decides if the
        action is available. Defaults to a function that always returns ``True``.
    schema : dict
        JSON schema specifying input accepted by the action.
        This is optional and can be left ``null`` if no input is taken.

    Returns
    -------
    function
        To be used as decorator for the callback function.
    """
    mem = {"registered": False}  # workaround nonlocal missing in 2.x

    def register_callback(cb):
        assert isinstance(cb, FunctionType), 'callback must be a function'
        assert isinstance(symbol, basestring), 'symbol must be a string'
        assert 1 <= len(symbol) <= 25, 'symbol must be between 1 and 25 characters'
        assert not mem['registered'], 'register_callback_action must be used as decorator'
        assert cb.__name__ not in callbacks, 'callback name {} is not unique'.format(cb.__name__)
        source_path = os.path.relpath(inspect.stack()[1][1], GECKO)

        @register_task_action(name, title, description, order, context, schema)
        def build_callback_action_task(parameters):
            if not available(parameters):
                return None

            match = re.match(r'https://(hg.mozilla.org)/(.*?)/?$', parameters['head_repository'])
            if not match:
                raise Exception('Unrecognized head_repository')
            repo_scope = 'assume:repo:{}/{}:*'.format(
                match.group(1), match.group(2))

            task_group_id = os.environ.get('TASK_ID', slugid())

            return {
                'created': {'$fromNow': ''},
                'deadline': {'$fromNow': '12 hours'},
                'expires': {'$fromNow': '1 year'},
                'metadata': {
                    'owner': 'mozilla-taskcluster-maintenance@mozilla.com',
                    'source': '{}/raw-file/{}/{}'.format(
                        parameters['head_repository'], parameters['head_rev'], source_path,
                    ),
                    'name': 'Action: {}'.format(title),
                    'description': 'Task executing callback for action.\n\n---\n' + description,
                },
                'workerType': 'gecko-{}-decision'.format(parameters['level']),
                'provisionerId': 'aws-provisioner-v1',
                'taskGroupId': task_group_id,
                'schedulerId': 'gecko-level-{}'.format(parameters['level']),
                'scopes': [
                    repo_scope,
                ],
                'tags': {
                    'createdForUser': parameters['owner'],
                    'kind': 'action-callback',
                },
                'routes': [
                    'tc-treeherder.v2.{}.{}.{}'.format(
                        parameters['project'], parameters['head_rev'], parameters['pushlog_id']),
                    'tc-treeherder-stage.v2.{}.{}.{}'.format(
                        parameters['project'], parameters['head_rev'], parameters['pushlog_id']),
                    'index.gecko.v2.{}.pushlog-id.{}.actions.${{ownTaskId}}'.format(
                        parameters['project'], parameters['pushlog_id'])
                ],
                'payload': {
                    'env': {
                        'GECKO_BASE_REPOSITORY': 'https://hg.mozilla.org/mozilla-unified',
                        'GECKO_HEAD_REPOSITORY': parameters['head_repository'],
                        'GECKO_HEAD_REF': parameters['head_ref'],
                        'GECKO_HEAD_REV': parameters['head_rev'],
                        'HG_STORE_PATH': '/builds/worker/checkouts/hg-store',
                        'ACTION_TASK_GROUP_ID': task_group_id,
                        'ACTION_TASK_ID': {'$json': {'$eval': 'taskId'}},
                        'ACTION_TASK': {'$json': {'$eval': 'task'}},
                        'ACTION_INPUT': {'$json': {'$eval': 'input'}},
                        'ACTION_CALLBACK': cb.__name__,
                        'ACTION_PARAMETERS': {'$json': {'$eval': 'parameters'}},
                        'TASKCLUSTER_CACHES': '/builds/worker/checkouts',
                    },
                    'artifacts': {
                        'public': {
                            'type': 'directory',
                            'path': '/builds/worker/artifacts',
                            'expires': {'$fromNow': '1 year'},
                        },
                    },
                    'cache': {
                        'level-{}-checkouts-sparse-v1'.format(parameters['level']):
                            '/builds/worker/checkouts',
                    },
                    'features': {
                        'taskclusterProxy': True,
                        'chainOfTrust': True,
                    },
                    'image': docker_image('decision'),
                    'maxRunTime': 1800,
                    'command': [
                        '/builds/worker/bin/run-task',
                        '--vcs-checkout=/builds/worker/checkouts/gecko',
                        '--sparse-profile=build/sparse-profiles/taskgraph',
                        '--', 'bash', '-cx',
                        """\
cd /builds/worker/checkouts/gecko &&
ln -s /builds/worker/artifacts artifacts &&
./mach --log-no-times taskgraph action-callback""",
                    ],
                },
                'extra': {
                    'treeherder': {
                        'groupName': 'action-callback',
                        'groupSymbol': 'AC',
                        'symbol': symbol,
                    },
                    'parent': task_group_id,
                    'action': {
                        'name': name,
                        'context': {
                            'taskGroupId': task_group_id,
                            'taskId': {'$eval': 'taskId'},
                            'input': {'$eval': 'input'},
                            'parameters': {'$eval': 'parameters'},
                        },
                    },
                },
            }
        mem['registered'] = True
        callbacks[cb.__name__] = cb
    return register_callback


def render_actions_json(parameters):
    """
    Render JSON object for the ``public/actions.json`` artifact.

    Parameters
    ----------
    parameters : taskgraph.parameters.Parameters
        Decision task parameters.

    Returns
    -------
    dict
        JSON object representation of the ``public/actions.json`` artifact.
    """
    assert isinstance(parameters, Parameters), 'requires instance of Parameters'
    result = []
    for action in sorted(get_actions(), key=lambda action: action.order):
        task = action.task_template_builder(parameters)
        if task:
            assert is_json(task), 'task must be a JSON compatible object'
            res = {
                'kind': 'task',
                'name': action.name,
                'title': action.title,
                'description': action.description,
                'context': action.context,
                'schema': action.schema,
                'task': task,
            }
            if res['schema'] is None:
                res.pop('schema')
            result.append(res)
    return {
        'version': 1,
        'variables': {
            'parameters': dict(**parameters),
        },
        'actions': result,
    }


def trigger_action_callback(task_group_id, task_id, task, input, callback, parameters,
                            test=False):
    """
    Trigger action callback with the given inputs. If `test` is true, then run
    the action callback in testing mode, without actually creating tasks.
    """
    cb = get_callbacks().get(callback, None)
    if not cb:
        raise Exception('Unknown callback: {}. Known callbacks: {}'.format(
            callback, get_callbacks().keys()))

    if test:
        create.testing = True
        taskcluster.testing = True

    cb(Parameters(**parameters), input, task_group_id, task_id, task)


@memoize
def _load():
    # Load all modules from this folder, relying on the side-effects of register_
    # functions to populate the action registry.
    actions_dir = os.path.dirname(__file__)
    for f in os.listdir(actions_dir):
        if f.endswith('.py') and f not in ('__init__.py', 'registry.py', 'util.py'):
            __import__('taskgraph.actions.' + f[:-3])
        if f.endswith('.yml'):
            with open(os.path.join(actions_dir, f), 'r') as d:
                frontmatter, template = yaml.safe_load_all(d)
                register_task_action(**frontmatter)(lambda _: template)
    return callbacks, actions


def get_callbacks():
    return _load()[0]


def get_actions():
    return _load()[1]
