"""Dispatch to the holland mysqldump plugin"""

import os
import time
import signal
import logging
from holland.lib.mysql import connect, MySQLError, PassiveMySQLClient
from ._mysqld import generate_server_config, MySQLServer, locate_mysqld_exe

LOG = logging.getLogger(__name__)

class MySQLDumpDispatchAction(object):
    def __init__(self, mysqldump_plugin, mysqld_config):
        self.mysqldump_plugin = mysqldump_plugin
        self.mysqld_config = mysqld_config

    def __call__(self, event, snapshot_fsm, snapshot):
        LOG.info("Handing-off to mysqldump plugin")
        datadir = self.mysqld_config['datadir']
        # find a mysqld executable to use
        mysqld_exe = locate_mysqld_exe(self.mysqld_config)

        self.mysqld_config['log-error'] = 'holland_lvm.log'
        socket = os.path.join(datadir, 'holland_mysqldump.sock')
        self.mysqld_config['socket'] = socket
        # patch up socket in plugin
        self.mysqldump_plugin.config['mysql:client']['socket'] = socket
        self.mysqldump_plugin.mysql_config['client']['socket'] = socket
        # set pidfile (careful to not overwrite current one)
        self.mysqld_config['pid-file'] = os.path.join(datadir,
                                                      'holland_lvm.pid')
        mycnf_path = os.path.join(datadir, 'my.bootstrap.cnf')
        # generate a my.cnf to pass to the mysqld bootstrap
        my_conf = generate_server_config(self.mysqld_config, mycnf_path)

        # log-bin is disabled to avoid conflict with the normal mysqld process
        self.mysqldump_plugin.config['mysqldump']['bin-log-position'] = False

        mysqld = MySQLServer(mysqld_exe, my_conf)
        mysqld.start(bootstrap=False)
        LOG.info("Waiting for %s to start", mysqld_exe)

        try:
            wait_for_mysqld(self.mysqldump_plugin.mysql_config['client'],
                            mysqld)
            LOG.info("%s accepting connections on unix socket %s", mysqld_exe, socket)
            self.mysqldump_plugin.backup()
        finally:
            mysqld.kill(signal.SIGKILL) # DIE DIE DIE
            mysqld.stop() # we dont' really care about the exit code, if mysqldump ran smoothly :)

def wait_for_mysqld(config, mysqld):
    client = connect(config, PassiveMySQLClient)
    LOG.debug("connect via client %r", config['socket'])
    while mysqld.process.poll() is None:
        try:
            client.connect()
            client.ping()
            LOG.debug("Ping succeeded")
        except MySQLError:
            time.sleep(0.75)
            continue
        else:
            break
    client.disconnect()
