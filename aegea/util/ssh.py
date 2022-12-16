import errno
import os
import sys

from paramiko import SSHClient

from .. import logger


class AegeaSSHClient(SSHClient):
    def check_call(self, *args, **kwargs):
        sys.stdout.write(self.check_output(*args, **kwargs))

    def check_output(self, command, input_data=None, stderr=sys.stderr):
        logger.debug('Running "%s"', command)
        ssh_stdin, ssh_stdout, ssh_stderr = self.exec_command(command)
        if input_data is not None:
            ssh_stdin.write(input_data)
        exit_code = ssh_stdout.channel.recv_exit_status()
        stderr.write(ssh_stderr.read().decode("utf-8"))
        if exit_code != os.EX_OK:
            raise Exception('Error while running "{}": {}'.format(command, errno.errorcode.get(exit_code)))
        return ssh_stdout.read().decode("utf-8")
