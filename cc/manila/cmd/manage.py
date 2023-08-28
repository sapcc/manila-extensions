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

import pprint
import sys

from oslo_config import cfg
from oslo_log import log
from oslo_utils import timeutils

from manila.common import config, constants  # Need to register global_opts  # noqa
from manila import context
from manila import db
from manila.db.sqlalchemy import models
from manila.i18n import _
from manila import utils
from manila import version

CONF = cfg.CONF

HOST_UPDATE_HELP_MSG = ("A host string is of the format 'HostA@BackendB' "
                        "(without the #Pool, this part would be ignored). "
                        "Provide only the host name (ex: 'HostA') to update "
                        "the hostname part of the host string. ")
HOST_UPDATE_CURRENT_HOST_HELP = ("Current share server host name. %s" %
                                 HOST_UPDATE_HELP_MSG)
HOST_UPDATE_NEW_HOST_HELP = ("New share server host name. %s" %
                             HOST_UPDATE_HELP_MSG)


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
            if '@' not in new_host:
                err = "%(chost)s specifies a backend but %(nhost)s does not."
        if err:
            print(err % {'chost': current_host, 'nhost': new_host})
            sys.exit(1)

    @staticmethod
    def _update_host_values(host, current_host, new_host):
        updated_host = host.replace(current_host, new_host)
        # remove the pool part, we don't know the target pool
        # the driver will find out (see _ensure_share_instance_has_pool method)
        updated_host_without_pool = updated_host.split('#')[0]
        updated_values = {
            'host': updated_host_without_pool,
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

        current_host_without_pool = current_host.split('#')[0]
        new_host_without_pool = new_host.split('#')[0]
        # check that new host is valid and up
        utils.validate_service_host(ctxt, new_host_without_pool)

        if current_host_without_pool in server_host:
            db.share_server_update(
                ctxt, uuid,
                self._update_host_values(
                    server_host,
                    current_host,
                    new_host
                )
            )

        for si in db.share_instances_get_all_by_share_server(ctxt, uuid):
            si_host = si['host']
            if current_host_without_pool in si_host:
                db.share_instance_update(
                    ctxt, si['id'],
                    self._update_host_values(si_host, current_host, new_host)
                )

        ports = db.network_allocations_get_for_share_server(ctxt, uuid)
        port_new_host = new_host_without_pool.split('@')[0]
        for port in ports:
            self._update_port_host_id(port['id'], port_new_host)

    @args('uuid', help='share server id')
    @args('skip_reason', help='comment, why ensure share server is skipped')
    def server_set_skip_ensure(self, uuid, skip_reason):
        """Add skip ensure flag to share server backend details.

        Similar to the snapmirror flag for shares that excludes them from
        ensure runs (also excludes them from castellum autoscaling).
        Sometimes we don't want 'ensure', because we manually set things on
        the storage backend.
        """
        ctxt = context.get_admin_context()
        server_details = {
            'skip_ensure': 'yes',
            'skip_ensure_comment': skip_reason,
        }
        backend_details = db.share_server_backend_details_set(ctxt, uuid,
                                                              server_details)
        print('added share server backend details:')
        pprint.pprint(backend_details)

    @args('uuid', help='share server id')
    def server_unset_skip_ensure(self, uuid):
        """Undo skip ensure flag on share server backend details.

        Make sure 'ensure' is running again for this share server.
        Unsets the 'skip_ensure' and 'skip_ensure_comment' keys.
        """
        ctxt = context.get_admin_context()
        share_server = db.share_server_get(ctxt, uuid)
        server_details = share_server.backend_details
        server_details.pop('skip_ensure', None)
        server_details.pop('skip_ensure_comment', None)
        print('remaining share server backend details:')
        pprint.pprint(server_details)

        # This is suboptimal:
        # we remove all details and apply the ones that should remain.
        # Could be improved by adding a method in the db layer that lets us
        # remove single keys from backend details.
        db.share_server_backend_details_delete(ctxt, uuid)
        db.share_server_backend_details_set(ctxt, uuid, server_details)

    @args('uuid', help='share id')
    def undelete(self, uuid):
        """Restore soft-deleted share and its relations.

        The following will be restored:

        Share
        ShareAccessMapping
        ShareAccessRulesMetadata
        ShareInstance
        ShareInstanceAccessMapping
        ShareInstanceExportLocations
        ShareInstanceExportLocationsMetadata
        ShareMetadata

        Not handled:

        Message
        QuotaUsage
        Reservation

        Caveats:
        Currently this restores everything, not looking at the dates
        so things that have not been purged will become alive again.
        This may be wrong if those had been intentionally deleted
        in a short timeframe before the unintended delete action.
        Especially in case of access rules the result should be verified.
        """
        share_instance_ids = []
        # FIXME: there is a bug with the read_deleted option
        # the behaviour of 'yes' and 'only' is flipped
        # we want to read both: deleted and non-deleted entries
        # so we currently have to specify 'only'
        ctxt = context.get_admin_context(read_deleted='only')
        # have to work on share instances before shares
        # because manila.db.sqlalchemy.models.Share.instance()
        # is hardcoded to ignore deleted entries
        session = db.IMPL.get_session()
        share_instances = db.share_instances_get_all_by_share(ctxt, uuid)
        for share_instance in share_instances:
            db.share_instance_update(
                ctxt, share_instance.id,
                {'deleted': 'False',
                    'status': constants.STATUS_AVAILABLE,
                    'access_rules_status': constants.SHARE_INSTANCE_RULES_SYNCING
            })
            share_instance_ids.append(share_instance.id)

            share_instance_access_rules = db.IMPL.model_query(
                ctxt,
                models.ShareInstanceAccessMapping,
                session=session
            ).filter_by(
                share_instance_id=share_instance.id
            ).all()

            for share_instance_access_rule in share_instance_access_rules:
                share_instance_access_rule.update({
                    'deleted': 'False',
                    'state': constants.ACCESS_STATE_QUEUED_TO_APPLY
                })
                share_instance_access_rule.save(session)

        # deleted export locations are hard go get, so we need to use IMPL
        # FIXME: remove read_deleted="no" from _share_export_locations_get
        # skip el_metadata_bare since it is also hardcoded to non deleted
        export_locations = db.IMPL.model_query(
            ctxt,
            models.ShareInstanceExportLocations,
            session=session
        ).filter(models.ShareInstanceExportLocations.share_instance_id.in_(
            share_instance_ids)
        ).all()

        for export_location in export_locations:
            export_location.update({'deleted': 0})
            export_location.save(session)
            export_location_metadata = db.IMPL.model_query(
                ctxt,
                models.ShareInstanceExportLocationsMetadata,
                session=session
            ).filter_by(
                export_location_id=export_location.id,
            ).all()

            for el_metadatum in export_location_metadata:
                el_metadatum.update({'deleted': 0})
                el_metadatum.save(session)

        access_rules = db.IMPL.model_query(
            ctxt,
            models.ShareAccessMapping,
            session=session
        ).filter_by(
            share_id=uuid
        ).all()

        for access_rule in access_rules:
            access_rule.update({'deleted': 'False'})
            access_rule.save(session)

            access_rule_metadata = db.IMPL.model_query(
                ctxt,
                models.ShareAccessRulesMetadata,
                session=session
            ).filter_by(
                access_id=access_rule.id,
            ).all()

            for access_rule_metadatum in access_rule_metadata:
                access_rule_metadatum.update({'deleted': 'False'})
                access_rule_metadatum.save(session)

        share_metadata = db.IMPL.model_query(
            ctxt,
            models.ShareMetadata,
            session=session
        ).filter_by(
            share_id=uuid
        ).all()

        for share_metadatum in share_metadata:
            share_metadatum.update({'deleted': 0})
            share_metadatum.save(session)

        db.share_update(ctxt, uuid, {'deleted': 'False'})

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
