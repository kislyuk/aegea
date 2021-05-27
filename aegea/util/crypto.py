import os, sys, binascii, subprocess

from botocore.exceptions import ClientError

from .. import logger
from .aws import resources, expect_error_codes

def new_ssh_key(bits=2048):
    from paramiko import RSAKey
    return RSAKey.generate(bits=bits)

def get_public_key_from_pair(key):
    return key.get_name() + " " + key.get_base64()

def key_fingerprint(key):
    hex_fp = binascii.hexlify(key.get_fingerprint()).decode()
    return key.get_name() + " " + ":".join(hex_fp[i:i + 2] for i in range(0, len(hex_fp), 2))

def get_ssh_key_path(name):
    return os.path.expanduser("~/.ssh/{}.pem".format(name))

def get_ssh_id():
    for line in subprocess.check_output(["ssh-add", "-L"]).decode().splitlines():
        return line

def ensure_local_ssh_key(name):
    from paramiko import RSAKey
    if os.path.exists(get_ssh_key_path(name)):
        ssh_key = RSAKey.from_private_key_file(get_ssh_key_path(name))
    else:
        logger.info("Creating key pair %s", name)
        ssh_key = new_ssh_key()
        os.makedirs(os.path.dirname(get_ssh_key_path(name)), exist_ok=True)
        ssh_key.write_private_key_file(get_ssh_key_path(name))
    return ssh_key

def add_ssh_key_to_agent(name):
    try:
        subprocess.check_call(["ssh-add", get_ssh_key_path(name)], timeout=5)
    except Exception as e:
        logger.warn("Failed to add %s to SSH keychain: %s. Connections may fail", get_ssh_key_path(name), e)

def ensure_ssh_key(name=None, base_name=__name__, verify_pem_file=True):
    if name is None:
        from getpass import getuser
        from socket import gethostname
        name = base_name + "." + getuser() + "." + gethostname().split(".")[0]

    try:
        ec2_key_pairs = list(resources.ec2.key_pairs.filter(KeyNames=[name]))
        if verify_pem_file and not os.path.exists(get_ssh_key_path(name)):
            msg = "Key {} found in EC2, but not in ~/.ssh."
            msg += " Delete the key in EC2, copy it to {}, or specify another key."
            raise KeyError(msg.format(name, get_ssh_key_path(name)))
    except ClientError as e:
        expect_error_codes(e, "InvalidKeyPair.NotFound")
        ec2_key_pairs = None

    if not ec2_key_pairs:
        ssh_key = ensure_local_ssh_key(name)
        resources.ec2.import_key_pair(KeyName=name,
                                      PublicKeyMaterial=get_public_key_from_pair(ssh_key))
        logger.info("Imported SSH key %s", get_ssh_key_path(name))
    add_ssh_key_to_agent(name)
    return name

def hostkey_line(hostnames, key):
    from paramiko import hostkeys
    return hostkeys.HostKeyEntry(hostnames=hostnames, key=key).to_line()

def add_ssh_host_key_to_known_hosts(host_key_line):
    ssh_known_hosts_path = os.path.expanduser("~/.ssh/known_hosts")
    with open(ssh_known_hosts_path, "a") as fh:
        fh.write(host_key_line)
