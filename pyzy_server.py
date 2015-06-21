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
import time
import traceback

def socket_name():
  username = pwd.getpwuid(os.getuid()).pw_name
  return '/tmp/pyzy-%s.sock' % username

class PyZySystemExit(SystemExit):
  pass

def pyzy_exit(return_code):
  raise PyZySystemExit(return_code)

class PyZyServer(object):
  sock = None

  def connect(self):
    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    self.sock.bind(socket_name())
    self.sock.listen(5)

  def recv_int(self, client_sock):
    return int(struct.unpack('!I', client_sock.recv(4))[0])

  def recv_str(self, client_sock):
    strlen = self.recv_int(client_sock)
    return client_sock.recv(strlen)
    
  def serve_forever(self):
    # keep track of scripts we have run
    script_set = set()
    while True:
      try:
        client_sock, addr = self.sock.accept()
      except socket.error as e:
        if e[0] == errno.EINTR:
          continue
      cwd = self.recv_str(client_sock)
      env = self.recv_str(client_sock)

      client_env = {}
      for line in env.split('\n'):
        line = line.strip()
        if line:
          key, value = line.split('=', 1)
          client_env[key] = value

      old_env = os.environ.copy()
      old_sys_path = sys.path[:]
      
      os.environ.update(client_env)
      argc = self.recv_int(client_sock)
      argv = [self.recv_str(client_sock) for x in range(argc)]
      try:
        script = os.path.abspath(argv[1])
      except:
        logging.exception('bad script')
        continue

      if not script in script_set:
        execfile(script, {})
        script_set.add(script)

      if os.fork() != 0:
        # parent process
        continue

      # child process
      sys.path[0:0] = client_env.get('PYTHONPATH', '').split(':')
      os.chdir(cwd)
      print script, client_env, argc, argv, os.getcwd()
      fd = client_sock.fileno()

      try:
        stdin_fd = _multiprocessing.recvfd(fd)
        stdout_fd = _multiprocessing.recvfd(fd)
        stderr_fd = _multiprocessing.recvfd(fd)
      except RuntimeError as e:
        print >> sys.stderr, e
        os._exit(1)

      os.dup2(stdin_fd, 0)
      os.dup2(stdout_fd, 1)
      os.dup2(stderr_fd, 2)
      sys.argv = argv[1:]
      sys.exit = pyzy_exit

      pid = os.getpid()
      client_sock.send(struct.pack('!I', pid))
      
      return_code = 0
      try:
        try:
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
        # else:
        #   for key in os.environ:
        #     if key in old_env:
        #       os.environ[key] = old_env[key]
        #     else:
        #       del os.environ[key]
        #   sys.path[:] = old_sys_path

def sigchld_handler(signum, frame):
  try:
    while os.waitpid(-1, os.WNOHANG)[0]:
      pass
  except OSError, e:
    if e[0] != errno.ECHILD:
      logging.error('waitpid failed: %s', e)
        
def main():
  try:
    logging.basicConfig()
    signal.signal(signal.SIGCHLD, sigchld_handler)
    server = PyZyServer()
    server.connect()
    server.serve_forever()
  except KeyboardInterrupt:
    pass
  finally:
    try:
      os.remove(socket_name())
    except Exception as e:
      print >> sys.stderr, e

if __name__ == '__main__':
  main()
