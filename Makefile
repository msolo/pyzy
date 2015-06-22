all: pyzy

pyzy_server.h: pyzy_server.py
	xxd -i $? $@

pyzy: pyzy_client.c pyzy_server.h
	gcc -std=c99 -D_POSIX_C_SOURCE=199309L -g -o $@ pyzy_client.c

