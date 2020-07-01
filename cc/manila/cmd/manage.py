# Copyright 2020 SAP SE
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from __future__ import print_function

import sys

from oslo_config import cfg
from oslo_log import log
from oslo_utils import timeutils

from manila.common import config  # Need to register global_opts  # noqa
from manila import context
from manila import db
from manila.i18n import _
from manila import version

CONF = cfg.CONF

HOST_UPDATE_HELP_MSG = ("A fully qualified host string is of the format "
                        "'HostA@BackendB#PoolC'. Provide only the host name "
                        "(ex: 'HostA') to update the hostname part of "
                        "the host string. Provide only the "
                        "host name and backend name (ex: 'HostA@BackendB') to "
                        "update the host and backend names.")
HOST_UPDATE_CURRENT_HOST_HELP = ("Current share host name. %s" %
                                 HOST_UPDATE_HELP_MSG)
HOST_UPDATE_NEW_HOST_HELP = "New share host name. %s" % HOST_UPDATE_HELP_MSG


# Decorators for actions
def args(*args, **kwargs):
    def _decorator(func):
        func.__dict__.setdefault('args', []).insert(0, (args, kwargs))
        return func
    return _decorator

class ShareCommands(object):

    @staticmethod
    def _validate_hosts(current_host, new_host):
        err = None
        if '@' in current_host:
            if '#' in current_host and '#' not in new_host:
                err = "%(chost)s specifies a pool but %(nhost)s does not."
            elif '@' not in new_host:
                err = "%(chost)s specifies a backend but %(nhost)s does not."
        if err:
            print(err % {'chost': current_host, 'nhost': new_host})
            sys.exit(1)

    @staticmethod
    def _update_host_values(host, current_host, new_host):
        updated_host = host.replace(current_host, new_host)
        updated_values = {
            'host': updated_host,
            'updated_at': timeutils.utcnow()
        }
        return updated_values

    @staticmethod
    def _update_port_host_id(port_id, host_id):
        from manila import exception
        from manila.network.neutron import api as neutron_api
        from neutronclient.common import exceptions as neutron_client_exc

        try:
            port_req_body = {'port': {'binding:host_id': host_id}}
            port = neutron_api.API().client.update_port(
                port_id, port_req_body).get('port', {})
            return port
        except neutron_client_exc.NeutronClientException as e:
            raise exception.NetworkException(code=e.status_code,
                                             message=e.message)

    @args('uuid', help='share server id')
    @args('--currenthost', required=True, help=HOST_UPDATE_CURRENT_HOST_HELP)
    @args('--newhost', required=True, help=HOST_UPDATE_NEW_HOST_HELP)
    def follow_server_migrate(self, uuid, current_host, new_host):
        """Modify the host name associated with a share server and its shares.

        Particularly to reflect if a migration was performed at the back-end
        level without driver assistance.
        The network allocation (neutron port) will also be updated.
        """
        self._validate_hosts(current_host, new_host)
        ctxt = context.get_admin_context()
        server = db.share_server_get(ctxt, uuid)
        server_host = server['host']

        server_current_host = current_host.split('#')[0]
        server_new_host = new_host.split('#')[0]

        if server_current_host in server_host:
            db.share_server_update(
                ctxt, uuid,
                self._update_host_values(
                    server_host,
                    server_current_host,
                    server_new_host
                )
            )

        for si in db.share_instances_get_all_by_share_server(ctxt, uuid):
            si_host = si['host']
            if current_host in si_host:
                db.share_instance_update(
                    ctxt, si['id'],
                    self._update_host_values(si_host, current_host, new_host)
                )

        ports = db.network_allocations_get_for_share_server(ctxt, uuid)
        port_new_host = server_new_host.split('@')[0]
        for port in ports:
            self._update_port_host_id(port['id'], port_new_host)


CATEGORIES = {
    'share': ShareCommands
}


def methods_of(obj):
    """Get all callable methods of an object that don't start with underscore.

    Returns a list of tuples of the form (method_name, method).
    """
    result = []
    for i in dir(obj):
        if callable(getattr(obj, i)) and not i.startswith('_'):
            result.append((i, getattr(obj, i)))
    return result


def add_command_parsers(subparsers):
    for category in CATEGORIES:
        command_object = CATEGORIES[category]()

        parser = subparsers.add_parser(category)
        parser.set_defaults(command_object=command_object)

        category_subparsers = parser.add_subparsers(dest='action')

        for (action, action_fn) in methods_of(command_object):
            parser = category_subparsers.add_parser(action)

            action_kwargs = []
            for args, kwargs in getattr(action_fn, 'args', []):
                parser.add_argument(*args, **kwargs)

            parser.set_defaults(action_fn=action_fn)
            parser.set_defaults(action_kwargs=action_kwargs)


category_opt = cfg.SubCommandOpt('category',
                                 title='Command categories',
                                 handler=add_command_parsers)


def get_arg_string(args):
    arg = None
    if args[0] == '-':
        # (Note)zhiteng: args starts with CONF.oparser.prefix_chars
        # is optional args. Notice that cfg module takes care of
        # actual ArgParser so prefix_chars is always '-'.
        if args[1] == '-':
            # This is long optional arg
            arg = args[2:]
        else:
            arg = args[1:]
    else:
        arg = args

    return arg


def fetch_func_args(func):
    fn_args = []
    for args, kwargs in getattr(func, 'args', []):
        arg = get_arg_string(args[0])
        fn_args.append(getattr(CONF.category, arg))

    return fn_args


def main():
    """Parse options and call the appropriate class/method."""
    CONF.register_cli_opt(category_opt)
    script_name = sys.argv[0]
    if len(sys.argv) < 2:
        print(_("\nOpenStack manila version: %(version)s\n") %
              {'version': version.version_string()})
        print(script_name + " category action [<args>]")
        print(_("Available categories:"))
        for category in CATEGORIES:
            print("\t%s" % category)
        sys.exit(2)

    try:
        log.register_options(CONF)
        CONF(sys.argv[1:], project='manila',
             version=version.version_string())
        log.setup(CONF, "manila")
    except cfg.ConfigFilesNotFoundError as e:
        cfg_files = e.config_files
        print(_("Failed to read configuration file(s): %s") % cfg_files)
        sys.exit(2)

    fn = CONF.category.action_fn

    fn_args = fetch_func_args(fn)
    fn(*fn_args)


if __name__ == '__main__':
    main()
