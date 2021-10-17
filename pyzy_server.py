from __future__ import print_function

import array
import atexit
import errno
import gc
import logging
import _multiprocessing
import os
import pwd
import random
import signal
import socket
import struct
import sys
import threading
import time
import traceback


def preload():
    import argparse
    import base64
    import csv
    import collections
    import datetime
    import email
    import fcntl
    import fnmatch
    import functools
    import glob
    import gzip
    import hashlib
    import io
    import itertools
    import json
    import operator
    import pickle
    import pipes
    import re
    import shlex
    import shutil
    import string
    import subprocess
    import tarfile
    import tempfile
    import urllib
    import zipfile
    import zlib

    if sys.version_info.major == 2:
        import ConfigParser
        import StringIO
        import cPickle
        import cStringIO
        import mimetools
        import urllib2
        import urlparse
    _gc_freeze()


default_python_path = "/usr/bin/python"


def socket_name():
    try:
        return os.environ["PYZY_SOCKET"]
    except KeyError:
        username = pwd.getpwuid(os.getuid()).pw_name
        return "/tmp/pyzy-%s.sock" % username


class TermInterrupt(Exception):
    pass


class PyZyError(Exception):
    pass


class PyZySystemExit(SystemExit):
    pass


def pyzy_exit(code_or_message):
    raise PyZySystemExit(code_or_message)


def recv(sock, size):
    while True:
        try:
            return sock.recv(size)
        except socket.error as e:
            # This socket is in blocking mode, but for reasons I don't
            # understand, we still get EAGAIN.
            if e.errno == errno.EAGAIN:
                continue
            raise


def _recv_fds(sock, msglen, maxfds):
    fds = array.array("i")  # Array of ints
    msg, ancdata, flags, addr = sock.recvmsg(
        msglen, socket.CMSG_LEN(maxfds * fds.itemsize)
    )
    for cmsg_level, cmsg_type, cmsg_data in ancdata:
        if cmsg_level == socket.SOL_SOCKET and cmsg_type == socket.SCM_RIGHTS:
            # Append data, ignoring any truncated integers at the end.
            fds.frombytes(cmsg_data[: len(cmsg_data) - (len(cmsg_data) % fds.itemsize)])
    return msg, list(fds)


# Python2/3 shenanigans
if hasattr(_multiprocessing, "recvfd"):

    def recvfd(sock):
        return _multiprocessing.recvfd(sock.fileno())


else:

    def recvfd(sock):
        # 48 the magic size of struct msghdr - not sure if there is a better
        # way to compute that.
        _, fds = _recv_fds(sock, 48, 1)
        return fds[0]


# Python2/3 shenanigans
def _execfile(fname, _globals=None):
    if sys.version_info.major == 2:
        execfile(fname, _globals)
    else:
        exec(compile(open(fname).read(), fname, "exec"), _globals)


# Python2/3 shenanigans
def _gc_freeze():
    if sys.version_info.major == 2:
        for gen in xrange(3):
            gc.collect(gen)
        gc.collect()
    else:
        gc.collect()
        gc.freeze()


# Some modules can get assume fork implies init, which isn't true any more.
# Rerun code for some codes to restore standard behavior.
def _fix_child_modules():
    # Remove any exit handlers - anything registered at this point is not
    # relevant.
    if sys.version_info.major == 2:
        del atexit._exithandlers[:]
    else:
        atexit._clear()

    # # the logging module is fun too - it has locks that might be held by a
    # # thread in the parent process. to prevent intermittent deadlock, you need
    # # to reset the locks. this just feels dirty.
    # logging._lock = None
    # logging._acquireLock()
    # for handler in logging._handlers:
    #   # this will overwrite the individual locks on each handler
    #   handler.createLock()
    # logging._releaseLock()

    # Make sure each process has a different random seed.
    random.seed()


class PyZyServer(object):
    sock = None

    def bind_and_listen(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(socket_name())
        self.sock.listen(5)

    def recv_int(self, client_sock):
        return int(struct.unpack("!I", recv(client_sock, 4))[0])

    def recv_str(self, client_sock):
        strlen = self.recv_int(client_sock)
        return recv(client_sock, strlen)

    def _child_globals(self, script, name="__main__"):
        return {
            "__file__": script,
            "__name__": name,
            "__package__": None,
            "__doc__": None,
        }

    def handle_connection(self, client_sock):
        client_sock.setblocking(True)
        cwd = self.recv_str(client_sock)

        client_env = {}
        env_blob = self.recv_str(client_sock)
        for kv in env_blob.rstrip(b"\0").split(b"\0"):
            x = kv.decode("utf8").split("=", 1)
            if len(x) == 2:
                k, v = x[0], x[1]
            else:
                k, v = x[0], ""
            client_env[k] = v

        old_env = os.environ.copy()
        old_sys_path = sys.path[:]

        os.environ.update(client_env)
        argc = self.recv_int(client_sock)
        argv = [self.recv_str(client_sock) for x in range(argc)]

        signal.alarm(int(os.environ.get("PYZY_MAX_IDLE_SECS", 600)))

        import_exc = None
        error_str = ""
        script = None

        try:
            script = os.path.normpath(os.path.join(cwd, argv[1]))
        except IndexError:
            error_str = "no script path specified in argv"

        client_python = client_env.get("PYZY_PYTHON", default_python_path)
        server_python = old_env.get("PYZY_PYTHON")

        if client_python != server_python:
            error_str = "PYZY_PYTHON mismatch: %s != %s" % (
                client_python,
                server_python,
            )
        elif not script in self.script_set:
            if "PYZY_CACHE_SCRIPT" in client_env:
                # Pass empty dict so __name__ != __main__.
                _execfile(script, self._child_globals(script, name="__pyzy_preload__"))
                self.script_set.add(script)
                # Protect ourselves against bad practices.
                threads = threading.enumerate()
                if len(threads) > 1:
                    import_exc = PyZyError(
                        "unsafe import created threads", script, repr(threads)
                    )
                # Try to keep memory from getting touched during gc to maximize sharing.
                _gc_freeze()

        pid = os.fork()
        if pid != 0:
            # parent process

            # Revert the environment and sys.path.
            for key in os.environ.keys():
                if key in old_env:
                    os.environ[key] = old_env[key]
                else:
                    del os.environ[key]
            sys.path[:] = old_sys_path
            # We can no longer safely fork, so die.
            if import_exc:
                raise import_exc
            return

        # child process
        try:
            pid = os.getpid()
            client_sock.send(struct.pack("!I", pid))
            # print script, client_env, argc, argv, os.getcwd()
            stdin_fd = recvfd(client_sock)
            stdout_fd = recvfd(client_sock)
            stderr_fd = recvfd(client_sock)
            os.dup2(stdin_fd, 0)
            os.dup2(stdout_fd, 1)
            os.dup2(stderr_fd, 2)

            sys.stdin = os.fdopen(0, "r")
            sys.stdout = os.fdopen(1, "w")
            sys.stderr = os.fdopen(2, "w")

            sys.path[0:0] = client_env.get("PYTHONPATH", "").split(":")
            os.chdir(cwd)
            if sys.version_info.major == 2:
                sys.argv = argv[1:]
            else:
                sys.argv = [x.decode("utf8") for x in argv[1:]]
            sys.exit = pyzy_exit
        except RuntimeError as e:
            print("pyzy:", e, file=sys.stderr)
            os._exit(1)

        return_code = 0
        try:
            try:
                # Make sure to relay any exception to the client - most of the
                # time the log will be /dev/null.
                if import_exc:
                    raise import_exc
                if error_str:
                    print("pyzy:", error_str, file=sys.stderr)
                    return_code = 255
                else:
                    _fix_child_modules()
                    _execfile(script, self._child_globals(script))
            except PyZySystemExit as e:
                try:
                    return_code = int(e.code)
                except ValueError:
                    print("pyzy: invalid exit code", e.code, file=sys.stderr)
                    return_code = 1
            except:
                traceback.print_exc()
                return_code = 1

            client_sock.send(struct.pack("!II", return_code, pid))
        finally:
            os._exit(return_code)

    def serve_forever(self):
        # keep track of scripts we have run
        self.script_set = set()
        while True:
            try:
                client_sock, addr = self.sock.accept()
                self.handle_connection(client_sock)
            except socket.error as e:
                if e.errno == errno.EINTR:
                    continue
                client_sock.close()
                logging.error("socket error: %s", e)


def sigchld_handler(signum, frame):
    try:
        while os.waitpid(-1, os.WNOHANG)[0]:
            pass
    except OSError as e:
        if e.errno != errno.ECHILD:
            logging.error("waitpid failed: %s", e)


def sigterm_handler(signum, frame):
    raise TermInterrupt


def main():
    try:
        logging.basicConfig(stream=sys.stderr, level=logging.INFO)
        signal.signal(signal.SIGCHLD, sigchld_handler)
        signal.signal(signal.SIGTERM, sigterm_handler)
        signal.signal(signal.SIGALRM, sigterm_handler)
        alarm_secs = int(os.environ.get("PYZY_MAX_IDLE_SECS", 600))
        signal.alarm(alarm_secs)
        preload()
        server = PyZyServer()
        server.bind_and_listen()
        server.serve_forever()
    except (KeyboardInterrupt, TermInterrupt):
        pass
    finally:
        try:
            os.remove(socket_name())
        except Exception as e:
            print("pyzy:", e, file=sys.stderr)


if __name__ == "__main__":
    main()
