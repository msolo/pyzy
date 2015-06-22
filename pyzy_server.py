#!/usr/local/bin/python

import errno
import logging
import _multiprocessing
import optparse
import os
import pwd
import signal
import socket
import struct
import sys
import threading
import time
import traceback

def socket_name():
  username = pwd.getpwuid(os.getuid()).pw_name
  return '/tmp/pyzy-%s.sock' % username

class TermInterrupt(Exception):
  pass

class PyZyError(Exception):
  pass

class PyZySystemExit(SystemExit):
  pass

def pyzy_exit(return_code):
  raise PyZySystemExit(return_code)

def recv(sock, size):
  while True:
    try:
      return sock.recv(size)
    except socket.error as e:
      # This socket is in blocking mode, but for reasons I don't
      # understand, we still get EAGAIN.
      if e[0] == errno.EAGAIN:
        continue
      raise

class PyZyServer(object):
  sock = None

  def connect(self):
    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    self.sock.bind(socket_name())
    self.sock.listen(5)

  def recv_int(self, client_sock):
    return int(struct.unpack('!I', recv(client_sock, 4))[0])

  def recv_str(self, client_sock):
    strlen = self.recv_int(client_sock)
    return recv(client_sock, strlen)

  def handle_connection(self, client_sock):
    client_sock.setblocking(True)
    cwd = self.recv_str(client_sock)
    envc = self.recv_int(client_sock)
    envv = [self.recv_str(client_sock) for x in range(envc)]

    client_env = dict(x.split('=', 1) for x in envv)

    old_env = os.environ.copy()
    old_sys_path = sys.path[:]

    os.environ.update(client_env)
    argc = self.recv_int(client_sock)
    argv = [self.recv_str(client_sock) for x in range(argc)]
    try:
      script = os.path.abspath(argv[1])
    except:
      logging.exception('bad script')
      return

    import_exc = None
    if not script in self.script_set:
      if 'PYZY_CACHE_SCRIPT' in client_env:
        # Pass empty dict so __name__ != __main__.
        execfile(script, {})
        self.script_set.add(script)
        # Protect ourselves against bad practices.
        threads = threading.enumerate()
        if len(threads) > 1:
          import_exc = PyZyError('unsafe import created threads', script, repr(threads))

    if os.fork() != 0:
      # parent process
      # Revert the environment and sys.path.
      for key in os.environ.keys():
        if key in old_env:
          os.environ[key] = old_env[key]
        else:
          del os.environ[key]
      sys.path[:] = old_sys_path
      # We can not longer safely fork, so die.
      if import_exc:
        raise import_exc
      return

    # child process
    try:
      sys.path[0:0] = client_env.get('PYTHONPATH', '').split(':')
      os.chdir(cwd)
      print script, client_env, argc, argv, os.getcwd()
      fd = client_sock.fileno()
      stdin_fd = _multiprocessing.recvfd(fd)
      stdout_fd = _multiprocessing.recvfd(fd)
      stderr_fd = _multiprocessing.recvfd(fd)
      os.dup2(stdin_fd, 0)
      os.dup2(stdout_fd, 1)
      os.dup2(stderr_fd, 2)
      
      sys.stdin = os.fdopen(0, 'r')
      sys.stdout = os.fdopen(1, 'w')
      sys.stderr = os.fdopen(2, 'w')
      
      sys.argv = argv[1:]
      sys.exit = pyzy_exit
      
      pid = os.getpid()
      client_sock.send(struct.pack('!I', pid))
    except RuntimeError as e:
      print >> sys.stderr, e
      os._exit(1)
      
    return_code = 0
    try:
      try:
        # Make sure to relay any exception to the client - most of the
        # time the log will be /dev/null.
        if import_exc:
          raise import_exc
        child_globals = {'__name__':'__main__'}
        execfile(script, child_globals)
      except PyZySystemExit, e:
        return_code = e[0]
      except Exception, e:
        traceback.print_exc()
        return_code = 1
      client_sock.send(struct.pack('!II', return_code, pid))
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
        if e[0] == errno.EINTR:
          continue
        client_sock.close()
        logging.exception('ERROR:')

def sigchld_handler(signum, frame):
  try:
    while os.waitpid(-1, os.WNOHANG)[0]:
      pass
  except OSError, e:
    if e[0] != errno.ECHILD:
      logging.error('waitpid failed: %s', e)

def sigterm_handler(signum, frame):
  raise TermInterrupt
        
def main():
  try:
    logging.basicConfig()
    signal.signal(signal.SIGCHLD, sigchld_handler)
    signal.signal(signal.SIGTERM, sigterm_handler)
    server = PyZyServer()
    server.connect()
    server.serve_forever()
  except (KeyboardInterrupt, TermInterrupt):
    pass
  finally:
    try:
      os.remove(socket_name())
    except Exception as e:
      print >> sys.stderr, e

if __name__ == '__main__':
  main()
