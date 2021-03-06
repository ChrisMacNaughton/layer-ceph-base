from charms import reactive
from charms.reactive import when, when_not, set_state, is_state
import charms.apt

from charms.ceph_base import (
    get_peer_units,
    get_mon_hosts,
    is_bootstrapped,
    is_quorum,
    get_running_osds,
    get_running_mds,
    assert_charm_supports_ipv6
)

from charmhelpers.core import hookenv
from charmhelpers.core.hookenv import (
    config,
    relation_ids,
    related_units,
    relation_get,
    status_set,
)

from charmhelpers.core.sysctl import create as create_sysctl

from charms_hardening.harden import harden


@harden()
@when_not('ceph.installed')
def install_ceph_base():
    charms.apt.add_source(config('source'), key=config('key'))
    charms.apt.queue_install(charms.ceph_base.PACKAGES)
    charms.apt.install_queued()
    set_state('ceph.installed')


@harden()
@when('config.changed', 'ceph.installed')
def config_changed():
    # # Check if an upgrade was requested
    # check_for_upgrade()
    # ^^ Need to handle this in the dependant charms

    if config('prefer-ipv6'):
        assert_charm_supports_ipv6()

    sysctl_dict = config('sysctl')
    if sysctl_dict:
        create_sysctl(sysctl_dict, '/etc/sysctl.d/50-ceph-charm.conf')


def assess_status():
    """Assess status of current unit"""
    statuses = set([])
    messages = set([])
    if is_state('ceph_mon.installed'):
        (status, message) = log_monitor()
        statuses.add(status)
        messages.add(message)
    if is_state('ceph_osd.installed'):
        (status, message) = log_osds()
        statuses.add(status)
        messages.add(message)
    if is_state('cephfs.started'):
        (status, message) = log_mds()
        statuses.add(status)
        messages.add(message)
    if 'blocked' in statuses:
        status = 'blocked'
    elif 'waiting' in statuses:
        status = 'waiting'
    else:
        status = 'active'
    message = '; '.join(messages)
    status_set(status, message)


def get_conf(name):
    for relid in relation_ids('mon'):
        for unit in related_units(relid):
            conf = relation_get(name,
                                unit, relid)
            if conf:
                return conf
    return None


def log_monitor():
    moncount = int(config('monitor-count'))
    units = get_peer_units()
    # not enough peers and mon_count > 1
    if len(units.keys()) < moncount:
        return ('blocked', 'Insufficient peer units to bootstrap'
                           ' cluster (require {})'.format(moncount))

    # mon_count > 1, peers, but no ceph-public-address
    ready = sum(1 for unit_ready in units.values() if unit_ready)
    if ready < moncount:
        return 'waiting', 'Peer units detected, waiting for addresses'

    # active - bootstrapped + quorum status check
    if is_bootstrapped() and is_quorum():
        return 'active', 'Unit is ready and clustered'
    else:
        # Unit should be running and clustered, but no quorum
        # TODO: should this be blocked or waiting?
        return 'blocked', 'Unit not clustered (no quorum)'
        # If there's a pending lock for this unit,
        # can i get the lock?
        # reboot the ceph-mon process


def log_osds():
    if not is_state('ceph_mon.installed'):
        # Check for mon relation
        if len(relation_ids('mon')) < 1:
            status_set('blocked', 'Missing relation: monitor')
            return 'blocked', 'Missing relation: monitor'

        # Check for monitors with presented addresses
        # Check for bootstrap key presentation
        monitors = get_mon_hosts()
        if len(monitors) < 1 or not get_conf('osd_bootstrap_key'):
            status_set('waiting', 'Incomplete relation: monitor')
            return 'waiting', 'Incomplete relation: monitor'

    # Check for OSD device creation parity i.e. at least some devices
    # must have been presented and used for this charm to be operational
    running_osds = get_running_osds()
    if not running_osds:
        return ('blocked',
                'No block devices detected using current configuration')
    else:
        return ('active',
                'Unit is ready ({} OSD)'.format(len(running_osds)))


def log_mds():
    if len(relation_ids('mon')) < 1:
        return 'blocked', 'Missing relation: monitor'
    running_mds = get_running_mds()
    if not running_mds:
        return 'blocked', 'No MDS detected using current configuration'
    else:
        return 'active', 'Unit is ready ({} MDS)'.format(len(running_mds))

# Per https://github.com/juju-solutions/charms.reactive/issues/33,
# this module may be imported multiple times so ensure the
# initialization hook is only registered once. I have to piggy back
# onto the namespace of a module imported before reactive discovery
# to do this.
if not hasattr(reactive, '_ceph_log_registered'):
    # We need to register this to run every hook, not just during install
    # and config-changed, to protect against race conditions. If we don't
    # do this, then the config in the hook environment may show updates
    # to running hooks well before the config-changed hook has been invoked
    # and the intialization provided an opertunity to be run.
    hookenv.atexit(assess_status)
    reactive._ceph_log_registered = True
