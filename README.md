# manila-extensions
SAP Converged Cloud OpenStack Manila Extensions

Provides a custom manila-manage-extension cli for manila.

The manila-manage-extension cli offers:

- `manila-manage-extension share follow_server_migrate SHARE_SERVER_UUID --currenthost CURRENT_HOST --newhost NEW_HOST` subcommand to adjust manila owned objects (share server, share instance, share, manila neutron port) after a volume migration was performed on the back-end storage box
- `manila-manage-extension share undelete SHARE_UUID` subcommand to restore a soft-deleted share and related objects

## Installation

Install the python package into the manila (virtual) environment

    pip install git+https://github.com/sapcc/manila-extensions.git
