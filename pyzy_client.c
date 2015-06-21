#include <sys/types.h>
#include <sys/socket.h>
#include <sys/uio.h>
#include <sys/un.h>

#include <errno.h>
#include <pwd.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "pyzy_server.h"

static char global_error_string[256];
static int remote_pid = 0;


int seterr(const char *fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(global_error_string, sizeof(global_error_string), fmt, ap);
  va_end(ap);
  return -1;
}

int debug_logf(const char *fmt, ...) {
  if (getenv("PYZY_DEBUG") == NULL) {
    return 0;
  }
  fprintf(stderr, "pyzy debug: ");
  va_list ap;
  va_start(ap, fmt);
  vfprintf(stderr, fmt, ap);
  va_end(ap);
  return 0;
}

int fatalf(const char *fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  vfprintf(stderr, fmt, ap);
  va_end(ap);
  exit(1);
}

char* unix_socket_path() {
  struct passwd* pwuid = getpwuid(getuid());
  if (pwuid == NULL) {
    fatalf("no passwd entry for uid: %d", getuid());
  }
  char* buf = malloc(1024);
  if (sprintf(buf,"/tmp/pyzy-%s.sock", pwuid->pw_name) < 0) {
    fatalf("unix_socket_path: failed sprintf");
  }
  return buf;
}

int open_unix_socket(const char *path) {
  int fd;
  struct sockaddr_un addr;
  socklen_t addrlen;
  size_t pathlen;
  size_t max_pathlen;

  memset(&addr, 0, sizeof(struct sockaddr_un));
  addr.sun_family = AF_UNIX;

  if (path == NULL)
    return seterr("The path cannot be NULL");
  if ((pathlen = strlen(path)) > (max_pathlen = sizeof(addr.sun_path) - 1))
    return seterr("The path cannot be longer than %u characters", max_pathlen);
  strcpy(addr.sun_path, path);

  addrlen = sizeof(addr);

  if ((fd = socket(AF_LOCAL, SOCK_STREAM, 0)) < 0)
    return seterr("socket() failed: %s", strerror(errno));
  if (connect(fd, (struct sockaddr *)&addr, addrlen) < 0)
    return seterr("connect() failed: %s", strerror(errno));
  return fd;
}

int send_fd(int unix_fd, int fd) {
  int n;
  char dummy_char;
  struct msghdr msg;
  struct iovec iov;
  struct cmsghdr *cmsg;
  char buf[CMSG_SPACE(sizeof(int))];

  iov.iov_base = &dummy_char;
  iov.iov_len = 1;

  memset(&msg, 0, sizeof(msg));
  msg.msg_control = buf;
  msg.msg_controllen = sizeof(buf);
  msg.msg_iov = &iov;
  msg.msg_iovlen = 1;

  cmsg = CMSG_FIRSTHDR(&msg);
  cmsg->cmsg_len = CMSG_LEN(sizeof(int));
  cmsg->cmsg_level = SOL_SOCKET;
  cmsg->cmsg_type = SCM_RIGHTS;
  * (int *)CMSG_DATA(cmsg) = fd;

  if ((n = sendmsg(unix_fd, &msg, 0)) < 0)
    return seterr("sendmsg() failed: %s", strerror(errno));

  return 0;
}

int send_int(int unix_fd, unsigned int _int) {
  unsigned int nbo_int = htonl(_int);
  ssize_t send_len = sizeof(unsigned int);
  ssize_t sent_bytes = send(unix_fd, &nbo_int, send_len, 0);
  if (sent_bytes != send_len) {
    return -1;
  }
  return 0;
}

int send_string(int unix_fd, char* _string) {
  unsigned int len = strlen(_string);
  int rc = send_int(unix_fd, len);
  if (rc) {
    return rc;
  }
  ssize_t sent_bytes = send(unix_fd, _string, len, 0);
  if (sent_bytes != len) {
    return -1;
  }
  return 0;
}

int send_launch_ctl(int unix_fd, int argc, char** argv) {
  int rc;
  
  char env[16*1024];
  char* environment_var_list[] = {
    "PYTHONPATH",
    NULL,
  };
  
  env[0] = '\0';
  char* env_var_name = NULL;
  char* env_val = NULL;
  int i = 0;
  while (1) {
    env_var_name = environment_var_list[i++];
    if (!env_var_name) {
      break;
    }
    env_val = getenv(env_var_name);
    if (!env_val) {
      continue;
    }
    strcat(env, env_var_name);
    strcat(env, "=");
    strcat(env, getenv(env_var_name));
    strcat(env, "\n");
  }
  debug_logf("env: %s\n", env);

  char cwd[2048];
  if (getcwd(cwd, sizeof(cwd)) == NULL) {
    return -1;
  }
  debug_logf("cwd: %s\n", cwd);

  if ((rc = send_string(unix_fd, cwd))) {
    return rc;
  }
  if ((rc = send_string(unix_fd, env))) {
    return rc;
  }
  if ((rc = send_int(unix_fd, argc))) {
    return rc;
  }
  for (i = 0; i < argc; i++) {
    if ((rc = send_string(unix_fd, argv[i]))) {
      return rc;
    }
  }
    
  if ((rc = send_fd(unix_fd, STDIN_FILENO))) {
    return rc;
  }
  if ((rc = send_fd(unix_fd, STDOUT_FILENO))) {
    return rc;
  }
  if ((rc = send_fd(unix_fd, STDERR_FILENO))) {
    return rc;
  }

  return 0;
}

int recv_int(int unix_fd, int* rint) {
  ssize_t bytes_read = recv(unix_fd, rint, sizeof(int), 0);
  if (bytes_read != sizeof(int)) {
    return -1;
  }
  *rint = ntohl(*rint);
  return 0;
}


int recv_return_code(int unix_fd, int* proc_rc, int* proc_pid) {
  int rc_pid_tuple[2];
  
  ssize_t bytes_read = recv(unix_fd, rc_pid_tuple, sizeof(rc_pid_tuple), 0);
  if (bytes_read != 8) {
    return -1;
  }
  *proc_rc = ntohl(rc_pid_tuple[0]);
  *proc_pid = ntohl(rc_pid_tuple[1]);
  debug_logf("recv_return_code: rc:%d pid:%d\n", *proc_rc, *proc_pid);
  return 0;
}

void signal_relay(int signal_num) {
  if (remote_pid > 0) {
    kill(remote_pid, signal_num);
    debug_logf("signal_relay: send %d to pid %d\n", signal_num, remote_pid);
  }
  //exit(1);
}

int main(int argc, char **argv) {
  int unix_fd;
  if ((unix_fd = open_unix_socket(unix_socket_path())) < 0) {
    fatalf("open_unix_socket failed: %s\n", global_error_string);
  }

  if (send_launch_ctl(unix_fd, argc, argv) < 0) {
    fatalf("send_launch_ctl failed: %s\n", global_error_string);
  }

  int rc;
  if ((rc = recv_int(unix_fd, &remote_pid))) {
    fatalf("recv_int remote_pid failed\n");
  }
  debug_logf("remote pid: %d\n", remote_pid);

  for (int sig = 1; sig < 32; sig++) {
    signal(sig, (void*)signal_relay);
  }
  
  int proc_rc = 0;
  int proc_pid = 0;
  if ((rc = recv_return_code(unix_fd, &proc_rc, &proc_pid))) {
    fatalf("recv_return_code failed\n");
  }
  return 0;
}
