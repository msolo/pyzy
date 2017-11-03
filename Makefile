all: pyzy

pyzy_server.h: pyzy_server.py
	xxd -i $? $@

pyzy: pyzy_client.c pyzy_server.h
	gcc -o $@ pyzy_client.c

