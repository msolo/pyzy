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

## Building pyzy_client:
```
#!bash
gcc pyzy_client.c -o pyzy_client
```

## Starting the Server:
```
#!bash
python pyzy_server.py
```

## Exec a script:

```
#!bash
pyzy_client my_script_file.py
```

The pyzy_client.py file is only a proof-of-concept, don't use it.