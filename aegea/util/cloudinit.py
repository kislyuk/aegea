from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys, base64, io, json, subprocess, random, string, tarfile
from collections import OrderedDict

from .. import logger
from . import gzip_compress_bytes
from .compat import StringIO
from .crypto import get_public_key_from_pair
from .aws import ensure_s3_bucket, clients

def add_file_to_cloudinit_manifest(src_path, path, manifest):
    with open(src_path, "rb") as fh:
        content = fh.read()
        manifest[path] = dict(path=path, permissions=oct(os.stat(src_path).st_mode)[-3:])
        try:
            manifest[path].update(content=content.decode())
        except UnicodeDecodeError:
            manifest[path].update(content=base64.b64encode(gzip_compress_bytes(content)), encoding="gz+b64")

def get_bootstrap_files(rootfs_skel_dirs, dest="cloudinit"):
    manifest = OrderedDict()
    targz = io.BytesIO()
    tar = tarfile.open(mode="w:gz", fileobj=targz) if dest == "tarfile" else None

    for rootfs_skel_dir in rootfs_skel_dirs:
        if rootfs_skel_dir == "auto":
            fn = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "rootfs.skel"))
        elif os.path.exists(rootfs_skel_dir):
            fn = os.path.abspath(os.path.normpath(rootfs_skel_dir))
        else:
            raise Exception("rootfs_skel directory {} not found".format(rootfs_skel_dir))
        logger.debug("Trying rootfs.skel: %s" % fn)
        if not os.path.exists(fn):
            raise Exception("rootfs_skel directory {} not found".format(fn))
        for root, dirs, files in os.walk(fn):
            for file_ in files:
                path = os.path.join("/", os.path.relpath(root, fn), file_)
                if dest == "cloudinit":
                    add_file_to_cloudinit_manifest(os.path.join(root, file_), path, manifest)
                elif dest == "tarfile":
                    tar.add(os.path.join(root, file_), path)

    if dest == "cloudinit":
        return list(manifest.values())
    elif dest == "tarfile":
        tar.close()
        return targz.getvalue()

def get_user_data(host_key=None, commands=None, packages=None, rootfs_skel_dirs=None, **kwargs):
    cloud_config_data = OrderedDict()
    cloud_config_data["packages"] = packages or []
    cloud_config_data["runcmd"] = commands or []
    cloud_config_data["write_files"] = get_bootstrap_files(rootfs_skel_dirs or [])
    for key in sorted(kwargs):
        cloud_config_data[key] = kwargs[key]
    if host_key is not None:
        buf = StringIO()
        host_key.write_private_key(buf)
        cloud_config_data["ssh_keys"] = dict(rsa_private=buf.getvalue(),
                                             rsa_public=get_public_key_from_pair(host_key))
    payload = encode_cloud_config_payload(cloud_config_data)
    if len(payload) >= 16384:
        logger.warn("Cloud-init payload is too large to be passed in user data, extracting rootfs.skel")
        upload_bootstrap_asset(cloud_config_data, rootfs_skel_dirs)
        payload = encode_cloud_config_payload(cloud_config_data)
    return payload

def encode_cloud_config_payload(cloud_config_data, gzip=True):
    # TODO: default=dict is for handling tweak.Config objects in the hierarchy.
    # TODO: Should subclass dict, not MutableMapping
    slug = "#cloud-config\n" + json.dumps(cloud_config_data, default=dict)
    return gzip_compress_bytes(slug.encode()) if gzip else slug

def upload_bootstrap_asset(cloud_config_data, rootfs_skel_dirs, use_cipher=False):
    key_name = "".join(random.choice(string.ascii_letters) for x in range(32))
    enc_key = "".join(random.choice(string.ascii_letters) for x in range(32))

    logger.info("Uploading bootstrap asset %s to S3", key_name)
    bucket = ensure_s3_bucket()
    url = clients.s3.generate_presigned_url(ClientMethod='get_object', Params=dict(Bucket=bucket.name, Key=key_name))
    cmd = "curl -s '{url}'".format(url=url)
    tarfile = get_bootstrap_files(rootfs_skel_dirs, dest="tarfile")

    if use_cipher:
        cipher = subprocess.Popen(["openssl", "aes-256-cbc", "-e", "-k", enc_key],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        encrypted_tarfile = cipher.communicate(tarfile)[0]
        bucket.upload_fileobj(io.BytesIO(encrypted_tarfile), key_name)
        cmd = cmd + " | openssl aes-256-cbc -d -k {key}".format(key=enc_key)
    else:
        bucket.upload_fileobj(io.BytesIO(tarfile), key_name)

    cmd = cmd + " | tar -xz --no-same-owner -C /"
    cmd = cmd + " && aws s3 rm s3://{bucket}/{key}".format(bucket=bucket.name, key=key_name)

    cloud_config_data["runcmd"].insert(0, cmd)

    del cloud_config_data["write_files"]
