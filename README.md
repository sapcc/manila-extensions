# manila-extensions
SAP Converged Cloud OpenStack Manila Extensions

Provides a custom manila-manage-extension cli for manila.

The manila-manage-extension cli offers:

- `manila-manage-extension share follow_server_migrate SHARE_SERVER_UUID --currenthost CURRENT_HOST --newhost NEW_HOST` subcommand to adjust manila owned objects (share server, share instance, share, manila neutron port) after a volume migration was performed on the back-end storage box
- `manila-manage-extension share undelete SHARE_UUID` subcommand to restore a soft-deleted share and related objects
- `manila-manage-extension share server_set_skip_ensure SHARE_SERVER_UUID SKIP_REASON` subcommand to set a flag at share server backend details to skip share server ensure runs, a reason should be provided
- `manila-manage-extension share server_unset_skip_ensure SHARE_SERVER_UUID` subcommand to undo skipping of share server ensure runs, removes respective keys at share server backend details

## Installation

Install the python package into the manila (virtual) environment

    pip install git+https://github.com/sapcc/manila-extensions.git
