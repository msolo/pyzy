# Python Zygote

Keep a warm Python interpreter process that can quickly fork off a new worker.
This makes small jobs less intense to start up.

## Server:

* Start up an interpreter
* Load most reasonable modules
* Listen on Unix domain socket
* accept connection
* fork a new child
* do child-specific initialization
* accept filename and arg list
* do file descriptor passing for stdin/stdout/stderr
* run to completion
* serialize return code

## Client:

* connect to unix domain socket
* serialize filename and arg list
* send filename, serialized arg list and fd 0,1,2
* wait for return code

## Building pyzy
```
make
```

## Debugging the Server
Starting a server by hand lets you see more errors.
```
#!bash
python pyzy_server.py
```

## Exec A Script

```
#!bash
pyzy my_script_file.py
```

## Controlling pyzy
There are few environment variables that are useful for tuning behavior.
PYZY_MAX_IDLE_SECS=600
 * The maximum number of seconds a pyzy process stays idle before exiting.
PYZY_CACHE_SCRIPT=1
 * Try to load all the code from the script into the main zygote process. The can be a bit messy with multiple conflicting imports. It's best combined with a dedicated server per-script which is done by using a custom PYZY_SOCKET.
PYZY_SOCKET=/tmp/pyzy-$LOGNAME
 * Use a custom socket to find the pyzy server process.
PYZY_PYTHON=/usr/bin/python
 * Use the proper python interpreter.

A lot of times you need a tiny wrapper script to wire this all up.
```
#!/usr/bin/env PYZY_PYTHON=/usr/bin/python3.6 PYZY_MAX_IDLE_SECS=1800 PYZY_CACHE_SCRIPT=1 PYZY_SOCKET=/tmp/pyzy-myscript /path/myscript.py
```
